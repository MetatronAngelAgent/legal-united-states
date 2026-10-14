#!/usr/bin/env python3
"""
cl_to_okf.py — all U.S. case law from CourtListener's quarterly bulk data (Free Law Project)
-> OKF v0.2 concepts, one per opinion.

CourtListener's bulk opinions file is the most complete open collection of American case law:
every Caselaw Access Project opinion (1658-2018) plus everything CourtListener has gathered from
court websites, PACER/RECAP, Public.Resource.Org, Lawbox, Columbia and others since — federal,
all 50 states, DC, territories, tribal and military courts, kept current every quarter.

Bulk files (public S3, https://com-courtlistener-storage.s3-us-west-2.amazonaws.com/bulk-data/):
  courts-<D>.csv.bz2 (tiny) · opinion-clusters-<D>.csv.bz2 (~2.5 GB) · citations-<D>.csv.bz2 (~0.1 GB)
  dockets-<D>.csv.bz2 (~5 GB) · opinions-<D>.csv.bz2 (~55 GB; streamed, never stored unless --keep)
CSV dialect: PostgreSQL COPY csv with ESCAPE '\\'.

Phase 1 builds a small SQLite join table in --cache (cluster -> case name, date, docket number,
court, citations). Phase 2 streams opinions and writes:
  <out>/case-law/<jurisdiction>/<court_id>/<year>/<case-slug>-<opinion_id>.md
Long opinions are split into part files. Re-running skips opinions already written, so an
interrupted run resumes. Clusters CourtListener has *blocked* (privacy de-indexing) are skipped.

Usage:
  python3 cl_to_okf.py --cache cl_cache --out legal                       # everything
  python3 cl_to_okf.py --cache cl_cache --out legal --scope federal,california
  python3 cl_to_okf.py --cache cl_cache --out /tmp/t --limit 20000         # sample test
Disk: full output is large (estimate printed after a sample run); see docs/coverage.md.
"""
import argparse, bz2, csv, datetime as dt, hashlib, html, io, json, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uslm_to_okf as U
from cap_to_okf import Resolver

TOOL = "cl_to_okf/1.0"
UA = "OKF-LawCorpus/1.0 (open legal-knowledge research; contact " + os.environ.get(
    "LEGAL_US_CONTACT", "see github.com/MetatronAngelAgent/legal-united-states") + ")"
BASE = "https://com-courtlistener-storage.s3-us-west-2.amazonaws.com/bulk-data"
csv.field_size_limit(1 << 31)

STATES = ["Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
          "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
          "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi",
          "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico",
          "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
          "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont",
          "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming", "District of Columbia",
          "Puerto Rico", "Guam", "Virgin Islands", "American Samoa", "Northern Mariana Islands"]
ABBR = {"N.J.": "New Jersey", "N.Y.": "New York", "Cal.": "California", "Pa.": "Pennsylvania", "Mass.": "Massachusetts",
        "Ohio": "Ohio", "Tex.": "Texas", "Fla.": "Florida", "Ill.": "Illinois", "Mich.": "Michigan", "D.C.": "District of Columbia"}
PREFIX = [("dc", "District of Columbia"), ("nj", "New Jersey"), ("ny", "New York"), ("cal", "California"),
          ("tex", "Texas"), ("fla", "Florida"), ("ill", "Illinois"), ("oh", "Ohio"), ("pa", "Pennsylvania"),
          ("mass", "Massachusetts"), ("mich", "Michigan"), ("conn", "Connecticut"), ("la", "Louisiana"),
          ("ga", "Georgia"), ("mo", "Missouri"), ("wash", "Washington"), ("va", "Virginia"), ("md", "Maryland")]
TYPES = {"010combined": "", "015unamimous": "Unanimous", "020lead": "", "025plurality": "Plurality",
         "030concurrence": "Concurrence", "035concurrenceinpart": "Concurrence in part",
         "040dissent": "Dissent", "050addendum": "Addendum", "060remittitur": "Remittitur",
         "070rehearing": "Rehearing", "080onthemerits": "On the merits", "090onmotiontostrike": "On motion to strike",
         "100trialcourt": "Trial court document"}
CITE_ORDER = {"5": 0, "1": 1, "2": 2, "8": 3, "3": 4, "4": 5, "7": 6, "6": 7, "9": 8}
TEXT_FIELDS = ["html_with_citations", "html_columbia", "html_lawbox", "xml_harvard", "html_anon_2020", "html", "plain_text"]

def norm_name(s):
    return re.sub(r"[^a-z]+", "-", s.lower()).strip("-")

def court_jur(c):
    j, name, cid = c["jurisdiction"], c["full_name"], c["id"]
    if j.startswith("F") or j == "MA" or cid in ("usjc",):
        return "federal"
    if j.startswith("TR"):
        return "tribal"
    if j in ("I", "T"):
        return None                                  # foreign / test courts: not U.S. law
    for st in sorted(STATES, key=len, reverse=True):
        if re.search(r"\b" + re.escape(st) + r"\b", name):
            if st == "Virginia" and "West Virginia" in name:
                continue
            return norm_name(st)
    for ab, st in ABBR.items():
        if ab in name:
            return norm_name(st)
    for pfx, st in PREFIX:
        if cid.startswith(pfx):
            return norm_name(st)
    return "other"

def opener(url_or_path, cache, keep):
    """Yield a text stream of a .csv.bz2 (local file, or streamed from S3; optionally kept)."""
    if os.path.exists(url_or_path):
        src = open(url_or_path, "rb")
    else:
        req = urllib.request.Request(url_or_path, headers={"User-Agent": UA})
        src = urllib.request.urlopen(req, timeout=600)
        if keep:
            fn = os.path.join(cache, os.path.basename(url_or_path))
            with open(fn + ".part", "wb") as f:
                shutil.copyfileobj(src, f, 1 << 22)
            os.replace(fn + ".part", fn)
            src = open(fn, "rb")
    if shutil.which("lbzip2") or shutil.which("pbzip2"):
        p = subprocess.Popen([shutil.which("lbzip2") or shutil.which("pbzip2"), "-dc"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        import threading
        def pump():
            while True:
                b = src.read(1 << 22)
                if not b: break
                p.stdin.write(b)
            p.stdin.close()
        threading.Thread(target=pump, daemon=True).start()
        raw = p.stdout
    else:
        raw = bz2.BZ2File(src)
    return io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")

def rows(stream):
    return csv.DictReader(stream, escapechar="\\", doublequote=False)

def src_of(cache, name, date):
    local = os.path.join(cache, f"{name}-{date}.csv.bz2")
    return local if os.path.exists(local) else f"{BASE}/{name}-{date}.csv.bz2"

# ---------- HTML -> paragraphs ----------
BLOCK = re.compile(r"(?is)</?(p|div|br|h[1-6]|li|blockquote|pre|tr|table|opinion|footnote|footnote_body|author|parties|docketnumber|court|decisiondate|attorneys|syllabus|headnotes|summary)\b[^>]*>")
def html_paras(h):
    h = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", h)
    h = re.sub(r'(?is)<span class="star-pagination"[^>]*>\s*\*?(\d+)\s*</span>', r" [*\1] ", h)
    h = re.sub(r'(?is)<page-number[^>]*>\s*\*?(\d+)\s*</page-number>', r" [*\1] ", h)
    h = re.sub(r'(?is)<footnotemark>(.*?)</footnotemark>', r"[^\1]", h)
    h = re.sub(r'(?is)<footnote\b[^>]*label="([^"]+)"[^>]*>', r"\n\n[^\1]: ", h)
    h = BLOCK.sub("\n\n", h)
    h = html.unescape(re.sub(r"(?s)<[^>]+>", "", h))
    return [U.normspace(p) for p in re.split(r"\n\s*\n", h) if U.normspace(p)]

def text_paras(t):
    t = t.replace("\r", "")
    parts = re.split(r"\n\s*\n", t)
    if len(parts) < 3:                                  # hard-wrapped text without blank lines
        parts = re.split(r"\n(?=\s{2,}|\t)", t)
    return [U.normspace(p) for p in parts if U.normspace(p)]

def opinion_paras(o):
    for f in TEXT_FIELDS:
        v = o.get(f) or ""
        if len(v.strip()) > 20:
            ps = text_paras(v) if f == "plain_text" else html_paras(v)
            if ps:
                return f, ps
    return None, []

# ---------- phase 1: metadata ----------
def build_meta(cache, date, keep, need_clusters=None):
    dbp = os.path.join(cache, f"cl-meta-{date}{'-sample' if need_clusters is not None else ''}.sqlite")
    done = dbp + ".done"
    if os.path.exists(done):
        return sqlite3.connect(dbp)
    if os.path.exists(dbp):
        os.remove(dbp)
    db = sqlite3.connect(dbp)
    db.executescript("""PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
      CREATE TABLE court(id TEXT PRIMARY KEY, full_name TEXT, short_name TEXT, cite TEXT, jur_code TEXT, jur TEXT, url TEXT);
      CREATE TABLE cluster(id INTEGER PRIMARY KEY, docket_id INTEGER, case_name TEXT, case_name_full TEXT, slug TEXT,
                           date_filed TEXT, approx INTEGER, status TEXT, judges TEXT, source TEXT, citation_count INTEGER,
                           harvard TEXT, blocked INTEGER);
      CREATE TABLE cite(cluster_id INTEGER, cite TEXT, ord INTEGER);
      CREATE TABLE docket(id INTEGER PRIMARY KEY, court_id TEXT, docket_number TEXT, blocked INTEGER);""")
    t0 = time.time()
    with opener(src_of(cache, "courts", date), cache, True) as s:
        for c in rows(s):
            db.execute("INSERT INTO court VALUES (?,?,?,?,?,?,?)", (c["id"], c["full_name"], c["short_name"],
                       c["citation_string"], c["jurisdiction"], court_jur(c), c["url"]))
    dockets, n = set(), 0
    with opener(src_of(cache, "opinion-clusters", date), cache, keep) as s:
        batch = []
        for c in rows(s):
            cid = int(c["id"])
            if need_clusters is not None and cid not in need_clusters:
                continue
            did = int(c["docket_id"]) if c["docket_id"] else None
            if did: dockets.add(did)
            batch.append((cid, did, c["case_name"] or c["case_name_short"] or c["case_name_full"][:300],
                          c["case_name_full"][:600], c["slug"], c["date_filed"], 1 if c["date_filed_is_approximate"] == "t" else 0,
                          c["precedential_status"], c["judges"][:300], c["source"], int(c["citation_count"] or 0),
                          c["filepath_json_harvard"], 1 if c["blocked"] == "t" else 0))
            n += 1
            if len(batch) >= 50000:
                db.executemany("INSERT OR REPLACE INTO cluster VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", batch); batch = []
                print(f"  clusters {n:,} ({time.time()-t0:.0f}s)", flush=True)
        db.executemany("INSERT OR REPLACE INTO cluster VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
    print(f"clusters: {n:,}", flush=True)
    with opener(src_of(cache, "citations", date), cache, keep) as s:
        batch, n = [], 0
        for c in rows(s):
            cl = int(c["cluster_id"])
            if need_clusters is not None and cl not in need_clusters:
                continue
            batch.append((cl, f"{c['volume']} {c['reporter']} {c['page']}", CITE_ORDER.get(c["type"], 9))); n += 1
            if len(batch) >= 100000:
                db.executemany("INSERT INTO cite VALUES (?,?,?)", batch); batch = []
        db.executemany("INSERT INTO cite VALUES (?,?,?)", batch)
    db.execute("CREATE INDEX cite_cl ON cite(cluster_id)")
    print(f"citations: {n:,}", flush=True)
    with opener(src_of(cache, "dockets", date), cache, keep) as s:
        batch, n = [], 0
        for d in rows(s):
            did = int(d["id"])
            if did not in dockets:
                continue
            batch.append((did, d["court_id"], (d["docket_number"] or "")[:200], 1 if d["blocked"] == "t" else 0)); n += 1
            if len(batch) >= 50000:
                db.executemany("INSERT OR REPLACE INTO docket VALUES (?,?,?,?)", batch); batch = []
        db.executemany("INSERT OR REPLACE INTO docket VALUES (?,?,?,?)", batch)
    print(f"dockets: {n:,} ({time.time()-t0:.0f}s)", flush=True)
    db.commit()
    open(done, "w").write(dt.datetime.now().isoformat())
    return db

# ---------- phase 2: opinions ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", default="legal")
    ap.add_argument("--date", default="2026-09-30", help="bulk snapshot date (quarterly)")
    ap.add_argument("--scope", default="all", help="all | comma list of jurisdictions: federal,california,new-york,tribal,...")
    ap.add_argument("--limit", type=int, default=0, help="test mode: only the first N opinions in the file")
    ap.add_argument("--keep", action="store_true", help="keep downloaded bulk files in --cache")
    ap.add_argument("--min-chars", type=int, default=100)
    ap.add_argument("--split-chars", type=int, default=6000)
    ap.add_argument("--include-blocked", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.cache, exist_ok=True)
    scope = {s.strip() for s in a.scope.split(",") if s.strip()}
    run_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    sample = None
    if a.limit:
        sp = os.path.join(a.cache, f"opinions-sample-{a.date}-{a.limit}.jsonl")
        if not os.path.exists(sp):
            with opener(src_of(a.cache, "opinions", a.date), a.cache, False) as s, open(sp + ".part", "w") as f:
                for i, o in enumerate(rows(s)):
                    if i >= a.limit: break
                    f.write(json.dumps(o) + "\n")
            os.replace(sp + ".part", sp)
        sample = sp
        need = {int(json.loads(l)["cluster_id"]) for l in open(sp)}
        db = build_meta(a.cache, a.date, a.keep, need)
    else:
        db = build_meta(a.cache, a.date, a.keep)

    courts = {r[0]: r for r in db.execute("SELECT id, full_name, short_name, cite, jur_code, jur, url FROM court")}
    res = Resolver(a.out)
    root = os.path.join(a.out, "case-law")
    stats, touched = Counter(), set()

    def lookup(cluster_id):
        r = db.execute("SELECT c.case_name, c.case_name_full, c.slug, c.date_filed, c.approx, c.status, c.judges, c.citation_count,"
                       " c.blocked, d.court_id, d.docket_number, d.blocked, c.docket_id FROM cluster c LEFT JOIN docket d ON d.id=c.docket_id"
                       " WHERE c.id=?", (cluster_id,)).fetchone()
        if not r: return None, []
        cites = [x[0] for x in db.execute("SELECT cite FROM cite WHERE cluster_id=? ORDER BY ord", (cluster_id,))]
        return r, cites

    it = (json.loads(l) for l in open(sample)) if sample else rows(opener(src_of(a.cache, "opinions", a.date), a.cache, a.keep))
    t0, n = time.time(), 0
    for o in it:
        n += 1
        if n % 20000 == 0:
            print(f"  {n:,} opinions read, {stats['written']:,} written ({time.time()-t0:.0f}s)", flush=True)
        try:
            cl = int(o["cluster_id"])
        except (ValueError, KeyError):
            stats["no_cluster"] += 1; continue
        meta, cites = lookup(cl)
        if not meta:
            stats["cluster_missing"] += 1; continue
        (case_name, case_full, slug, date_filed, approx, status, judges, ccount, cblocked,
         court_id, docket_no, dblocked, docket_id) = meta
        if (cblocked or dblocked) and not a.include_blocked:
            stats["blocked_skipped"] += 1; continue
        court = courts.get(court_id or "")
        jur = court[5] if court else "other"
        if jur is None:
            stats["foreign_or_test_court"] += 1; continue
        if "all" not in scope and jur not in scope:
            stats["out_of_scope"] += 1; continue
        year = (date_filed or "")[:4] or "undated"
        oid = o["id"]
        ddir = os.path.join(root, jur, U.slug(court_id or "unknown-court"), year)
        path = os.path.join(ddir, f"{(U.slug(slug or 'case')[:60]).strip('-') or 'case'}-{oid}.md")
        if os.path.exists(path):
            stats["already_written"] += 1; touched.add(ddir); continue
        field, paras = opinion_paras(o)
        body = "\n\n".join(paras)
        if len(body) < a.min_chars:
            stats["empty_or_short_skipped"] += 1; continue
        otype = TYPES.get(o.get("type") or "", (o.get("type") or "").lstrip("0123456789"))
        cite = cites[0] if cites else ""
        cname = case_name or "Unnamed case"
        court_name = court[1] if court else (court_id or "")
        label = cite or (f"No. {docket_no}" if docket_no else f"CourtListener opinion {oid}")
        title = f"{cname}, {label} ({(court[3] + ' ') if court and court[3] else ''}{year})" + (f" — {otype}" if otype else "")
        author = U.normspace(o.get("author_str") or "") or ("Per curiam" if o.get("per_curiam") == "t" else "")
        cited = res.links(body)
        auth = ""
        if cited:
            auth = "# Cited Authorities\n\n" + "\n".join((f"- [{c}]({p})" if p else f"- {c}") for c, p in cited) + "\n\n"
            stats["statute_citations"] += len(cited); stats["statute_citations_linked"] += sum(1 for _, p in cited if p)
        url = f"https://www.courtlistener.com/opinion/{cl}/{slug or 'case'}/"
        pairs = [
            ("type", "Judicial Opinion"), ("title", title[:300]),
            ("description", f"{court_name}, {date_filed or 'date unknown'}" + (f" ({otype})" if otype else "")),
            ("resource", url),
            ("tags", ["case-law", jur, court_id or "unknown-court"] + (["unpublished"] if status and status != "Published" else [])),
            ("citation", cite), ("parallel_citations", cites[1:12]),
            ("case_name", cname[:300]), ("case_name_full", (case_full or "")[:600]),
            ("court", court_name), ("court_id", court_id or ""), ("docket", (docket_no or "")[:200]),
            ("decision_date", date_filed or ""), ("date_is_approximate", ("raw", "true") if approx else None),
            ("opinion_type", otype or "Majority / combined"), ("opinion_author", author[:120]),
            ("judges", (judges or "")[:300]), ("precedential_status", status or ""),
            ("cited_by_count", ("raw", str(int(ccount))) if ccount else None),
            ("jurisdiction", jur), ("jurisdiction_level", "federal" if jur == "federal" else "tribal" if jur == "tribal" else "state"),
            ("license", "Public domain (judicial opinion); bulk data by Free Law Project / CourtListener"),
            ("status", "stable"),
            ("generated", {"by": TOOL, "at": run_at}),
            ("sources", ("raw", "\n  - { id: courtlistener, resource: " + U.yq(f"{BASE}/opinions-{a.date}.csv.bz2 (opinion {oid}, cluster {cl}, field {field})") +
                         ", title: " + U.yq(f"CourtListener bulk data {a.date}") + ", author: " + U.yq("process:free-law-project") + " }")),
            ("checksum", hashlib.sha256(body.encode()).hexdigest()),
        ]
        os.makedirs(ddir, exist_ok=True)
        touched.add(ddir)
        if len(body) <= a.split_chars * 2 or len(paras) < 2:
            md = U.emit_frontmatter(pairs) + "\n" + auth + "# Opinion\n\n" + body + "\n"
        else:
            chunks, cur, sz = [], [], 0
            for p in paras:
                if cur and sz + len(p) > a.split_chars:
                    chunks.append(cur); cur, sz = [], 0
                cur.append(p); sz += len(p) + 2
            if cur: chunks.append(cur)
            pdir = path[:-3]; os.makedirs(pdir, exist_ok=True)
            lk = []
            for k, c in enumerate(chunks, 1):
                pb = "\n\n".join(c); pt = f"{title[:250]} (part {k} of {len(chunks)})"
                pp = [(x, (pt if x == "title" else hashlib.sha256(pb.encode()).hexdigest() if x == "checksum" else v))
                      for x, v in pairs] + [("part_of", f"{k} of {len(chunks)}")]
                open(os.path.join(pdir, f"part-{k:03d}.md"), "w", encoding="utf-8").write(U.emit_frontmatter(pp) + "\n# Opinion\n\n" + pb + "\n")
                lk.append((f"{os.path.basename(pdir)}/part-{k:03d}.md", pt))
            md = (U.emit_frontmatter(pairs + [("parts", ("raw", str(len(chunks))))]) + "\n" + auth + "# Opinion\n\n" + paras[0] +
                  "\n\n# Parts\n\n" + "\n".join(f"- [{t}]({p})" for p, t in lk) + "\n")
            stats["split_into_parts"] += 1
        open(path + ".part", "w", encoding="utf-8").write(md)
        os.replace(path + ".part", path)
        stats["written"] += 1; stats[f"jur:{jur}"] += 1; stats[f"text:{field}"] += 1
    stats["read"] = n

    # indexes: case-law / jurisdiction / court / year
    def nk(s): return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s)]
    courts_touched = defaultdict(set)
    for d in touched:
        items = []
        for f in sorted(os.listdir(d), key=nk):
            if f.endswith(".md") and f != "index.md":
                t = re.search(r'^title: "(.*)"$', open(os.path.join(d, f), encoding="utf-8").read(3000), re.M)
                items.append(f"* [{t.group(1) if t else f}]({f})")
        cdir = os.path.dirname(d)
        open(os.path.join(d, "index.md"), "w", encoding="utf-8").write(
            f"# {courts.get(os.path.basename(cdir), [None, os.path.basename(cdir)])[1]} — {os.path.basename(d)}\n\n" + "\n".join(items) + "\n")
        courts_touched[os.path.dirname(cdir)].add(cdir)
    for jdir, cdirs in courts_touched.items():
        for cdir in cdirs:
            years = sorted((y for y in os.listdir(cdir) if os.path.isdir(os.path.join(cdir, y))), key=nk)
            cid = os.path.basename(cdir)
            open(os.path.join(cdir, "index.md"), "w", encoding="utf-8").write(
                f"# {courts.get(cid, [None, cid])[1]}\n\n" + "\n".join(f"* [{y}]({y}/)" for y in years) + "\n")
        cs = sorted(c for c in os.listdir(jdir) if os.path.isdir(os.path.join(jdir, c)))
        open(os.path.join(jdir, "index.md"), "w", encoding="utf-8").write(
            f"# Case law — {os.path.basename(jdir).replace('-', ' ').title()}\n\n" +
            "\n".join(f"* [{courts.get(c, [None, c])[1]}]({c}/)" for c in cs) + "\n")
    if os.path.isdir(root):
        open(os.path.join(root, "index.md"), "w", encoding="utf-8").write(
            "# Case Law\n\nAll U.S. courts — federal, every state, DC, territories, tribal and military — from CourtListener "
            f"bulk data ({a.date}; Free Law Project), which includes the full Caselaw Access Project. Judicial opinions are public domain.\n\n" +
            "\n".join(f"* [{j.replace('-', ' ').title()}]({j}/)" for j in sorted(os.listdir(root)) if os.path.isdir(os.path.join(root, j))) + "\n")
    ref = os.path.join(a.out, "references"); os.makedirs(ref, exist_ok=True)
    json.dump({"date": a.date, "scope": sorted(scope), "limit": a.limit, "stats": dict(stats)},
              open(os.path.join(ref, "ingest-stats-case-law-courtlistener.json"), "w"), indent=1)
    print("done:", json.dumps(dict(stats)), flush=True)

if __name__ == "__main__":
    main()

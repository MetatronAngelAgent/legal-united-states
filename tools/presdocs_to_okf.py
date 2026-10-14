#!/usr/bin/env python3
"""
presdocs_to_okf.py — Executive branch: presidential documents -> OKF v0.2 concepts, one per document.

Every Executive Order, Proclamation, Memorandum, Notice, Determination, Order, Directive and
Presidential Order published in the Federal Register (1994-present), with the metadata the
Office of the Federal Register keeps for them: EO / proclamation number, signing date,
president, and disposition notes ("Revokes: EO 13508", "Amended by: EO 14xxx").

Sources (both public domain, both allowed by robots.txt):
  metadata  federalregister.gov API v1 (documents.json, type PRESDOCU), queried month by month
            because the API stops paging at 2,000 results per query
  text      govinfo.gov per-document HTML of the official FR issue
            (content/pkg/FR-<date>/html/<frdoc>.htm). federalregister.gov's own full-text
            endpoints refuse many cloud IP ranges, govinfo does not.

Writes
  <out>/federal/executive/presidential-documents/<kind>/<year>/<slug>.md
     kind = executive-orders | proclamations | memoranda | notices | determinations | other
with indexes per kind/year rebuilt from disk, so incremental runs keep earlier documents.

Usage:
  python3 presdocs_to_okf.py --out legal --from 1994-01-01            # backfill (≈9,000 docs)
  python3 presdocs_to_okf.py --out legal --since 2026-10-13           # incremental (daily job)
Re-running skips documents already written unless --force. Stdlib only.
"""
import argparse, datetime as dt, hashlib, html, json, os, re, sys, time, urllib.parse, urllib.request, urllib.error
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uslm_to_okf as U

TOOL = "presdocs_to_okf/1.0"
UA = "OKF-LawCorpus/1.0 (open legal-knowledge research; contact " + os.environ.get(
    "LEGAL_US_CONTACT", "see github.com/MetatronAngelAgent/legal-united-states") + ")"
API = "https://www.federalregister.gov/api/v1/documents.json"
FIELDS = ["document_number", "title", "subtype", "presidential_document_number", "executive_order_number",
          "proclamation_number", "signing_date", "publication_date", "president", "citation",
          "disposition_notes", "executive_order_notes", "html_url", "pdf_url", "start_page", "end_page",
          "volume", "abstract"]
KINDS = {"Executive Order": "executive-orders", "Proclamation": "proclamations", "Memorandum": "memoranda",
         "Notice": "notices", "Determination": "determinations"}
KIND_TITLES = {"executive-orders": "Executive Orders", "proclamations": "Proclamations", "memoranda": "Memoranda",
               "notices": "Notices", "determinations": "Presidential Determinations",
               "other": "Other Presidential Documents (orders, directives, reorganization plans)"}

def get(url, tries=5, accept=None):
    for a in range(tries):
        try:
            h = {"User-Agent": UA}
            if accept: h["Accept"] = accept
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (404, 410) or a == tries - 1:
                raise
            time.sleep(min(120, 10 * (a + 1) ** 2) if e.code == 429 else 5 * (a + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if a == tries - 1:
                raise
            time.sleep(5 * (a + 1))

def months(start, end):
    d = start.replace(day=1)
    while d <= end:
        nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        yield max(d, start), min(nxt - dt.timedelta(days=1), end)
        d = nxt

def list_docs(start, end, delay):
    """Yield API records published between start and end (inclusive)."""
    for a, b in months(start, end):
        params = [("conditions[type][]", "PRESDOCU"), ("per_page", "1000"), ("order", "oldest"),
                  ("conditions[publication_date][gte]", a.isoformat()),
                  ("conditions[publication_date][lte]", b.isoformat())] + [("fields[]", f) for f in FIELDS]
        url = API + "?" + urllib.parse.urlencode(params)
        while url:
            j = json.loads(get(url, accept="application/json"))
            for r in j.get("results") or []:
                yield r
            url = j.get("next_page_url")
            time.sleep(delay)

PAGE = re.compile(r"\[\[Page (\d+)\]\]")
def govinfo_text(pubdate, frdoc):
    """Official text from govinfo's per-document HTML (<pre> block) -> paragraphs."""
    raw = get(f"https://www.govinfo.gov/content/pkg/FR-{pubdate}/html/{frdoc}.htm").decode("utf-8", "replace")
    m = re.search(r"(?is)<pre>(.*)</pre>", raw)
    t = html.unescape(re.sub(r"(?s)<[^>]+>", "", m.group(1) if m else raw))
    t = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", t.replace("\xa0", " "))   # GPO pads with NULs / CRs
    lines = t.split("\n")
    # drop the GPO header (everything up to and including the "[FR Doc No: …]" line)
    for i, l in enumerate(lines[:12]):
        if l.startswith("[FR Doc No"):
            lines = lines[i + 1:]; break
    # drop the issue's part cover page that precedes "Title 3-- The President"
    for i, l in enumerate(lines):
        if re.match(r"\s*Title 3--\s*$", l):
            lines = lines[i + 1:]
            if lines and lines[0].strip() == "The President":
                lines = lines[1:]
            break
    out, cur = [], []
    def flush():
        if cur:
            p = U.normspace(" ".join(cur)); cur.clear()
            if p: out.append(p)
    for l in lines:
        s = l.strip()
        pm = PAGE.fullmatch(s)
        if pm:
            cur.append(f"[*{pm.group(1)}]"); continue
        if not s:
            flush(); continue
        if re.match(r"\[FR Doc\.", s) or re.match(r"Filed \d+-\d+-\d+;", s) or re.match(r"(?i)billing code", s) or re.fullmatch(r"_+|-+", s):
            flush(); continue
        if re.match(r"Federal Register / Vol\.", s) or s == "Presidential Documents":
            continue
        cur.append(s)
    flush()
    # drop paragraphs that are only a page marker
    return [p for p in out if not re.fullmatch(r"\[\*\d+\]", p)]

def kind_of(r):
    return KINDS.get(r.get("subtype") or "", "other")

def number_of(r):
    return r.get("executive_order_number") or r.get("proclamation_number") or r.get("presidential_document_number") or ""

def doc_path(root, r):
    kind = kind_of(r)
    year = (r.get("signing_date") or r.get("publication_date") or "undated")[:4]
    num = number_of(r)
    stem = (f"eo-{num}" if kind == "executive-orders" and num else
            f"proclamation-{num}" if kind == "proclamations" and num else
            f"{U.slug(num)}-{r['document_number']}" if num else r["document_number"])
    title_slug = U.slug(r.get("title") or "")[:60].strip("-")
    return os.path.join(root, kind, year, f"{stem}-{title_slug}.md" if title_slug else f"{stem}.md")

def write_doc(root, r, paras, run_at, split_chars):
    kind = kind_of(r)
    num = number_of(r)
    sub = r.get("subtype") or "Presidential Document"
    pres = (r.get("president") or {}).get("name") or ""
    cite = r.get("citation") or f"FR Doc. {r['document_number']}"
    head = (f"Executive Order {num}" if kind == "executive-orders" and num else
            f"Proclamation {num}" if kind == "proclamations" and num else sub)
    title = f"{head} — {r.get('title') or ''} ({cite})"[:300]
    body = "\n\n".join(paras)
    notes = r.get("disposition_notes") or r.get("executive_order_notes") or ""
    rel = []
    for m in re.finditer(r"(Revokes|Revoked by|Amends|Amended by|Supersedes|Superseded by|See|Continued by|Continues)\s*:\s*([^;]+?)(?=;\s*[A-Z][a-z ]+:|$)", notes):
        rel.append((m.group(1), m.group(2).strip()))
    pairs = [
        ("type", f"Presidential {sub}" if not sub.startswith("Presidential") else sub),
        ("title", title),
        ("description", (r.get("abstract") or f"{sub} signed by {pres or 'the President'} on {r.get('signing_date') or 'unknown date'}")[:240]),
        ("resource", r.get("html_url") or f"https://www.federalregister.gov/d/{r['document_number']}"),
        ("tags", ["federal", "executive", "presidential-document", kind] + ([U.slug(pres)] if pres else [])),
        ("citation", cite), ("fr_document_number", r["document_number"]),
        ("document_subtype", sub), ("document_number", num),
        ("executive_order_number", r.get("executive_order_number") or ""),
        ("proclamation_number", r.get("proclamation_number") or ""),
        ("president", pres), ("signing_date", r.get("signing_date") or ""),
        ("publication_date", r.get("publication_date") or ""),
        ("disposition_notes", notes),
        ("branch", "executive"),
        ("jurisdiction", "united-states"), ("jurisdiction_level", "federal"),
        ("license", "Public domain (U.S. Government work)"),
        ("status", "stable"),
        ("generated", {"by": TOOL, "at": run_at}),
        ("sources", ("raw", "\n  - { id: govinfo-fr, resource: " +
                     U.yq(f"https://www.govinfo.gov/content/pkg/FR-{r.get('publication_date')}/html/{r['document_number']}.htm") +
                     ", title: " + U.yq(f"Federal Register {cite}") + ", author: " + U.yq("process:us-gpo-ofr") + " }" +
                     "\n  - { id: federalregister-api, resource: " + U.yq(f"https://www.federalregister.gov/api/v1/documents/{r['document_number']}.json") +
                     ", title: " + U.yq("Federal Register document metadata") + ", author: " + U.yq("process:us-ofr") + " }")),
        ("checksum", hashlib.sha256(body.encode()).hexdigest()),
    ]
    top = ""
    if rel:
        top = "# Disposition\n\n" + "\n".join(f"- **{k}:** {v}" for k, v in rel) + "\n\n"
    path = doc_path(root, r)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if len(body) <= split_chars * 2 or len(paras) < 2:
        md = U.emit_frontmatter(pairs) + "\n" + top + "# Text\n\n" + body + "\n"
    else:
        chunks, cur, sz = [], [], 0
        for p in paras:
            if cur and sz + len(p) > split_chars:
                chunks.append(cur); cur, sz = [], 0
            cur.append(p); sz += len(p) + 2
        if cur: chunks.append(cur)
        pdir = path[:-3]; os.makedirs(pdir, exist_ok=True)
        lk = []
        for k, c in enumerate(chunks, 1):
            pb = "\n\n".join(c); pt = f"{title[:250]} (part {k} of {len(chunks)})"
            pp = [(x, (pt if x == "title" else hashlib.sha256(pb.encode()).hexdigest() if x == "checksum" else v))
                  for x, v in pairs] + [("part_of", f"{k} of {len(chunks)}")]
            open(os.path.join(pdir, f"part-{k:03d}.md"), "w", encoding="utf-8").write(U.emit_frontmatter(pp) + "\n# Text\n\n" + pb + "\n")
            lk.append((f"{os.path.basename(pdir)}/part-{k:03d}.md", pt))
        open(os.path.join(pdir, "index.md"), "w", encoding="utf-8").write(
            f"# {title}\n\n" + "\n".join(f"* [{t}]({os.path.basename(p)})" for p, t in lk) + "\n")
        md = (U.emit_frontmatter(pairs + [("parts", ("raw", str(len(chunks))))]) + "\n" + top +
              "# Parts\n\n" + "\n".join(f"- [{t}]({p})" for p, t in lk) + "\n")
    open(path + ".part", "w", encoding="utf-8").write(md)
    os.replace(path + ".part", path)
    return path

def nk(s):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s)]

def rebuild_indexes(root, years_touched=None):
    """Indexes are rebuilt from what is on disk, so incremental runs never drop older entries."""
    if not os.path.isdir(root):
        return
    for kind in sorted(os.listdir(root)):
        kdir = os.path.join(root, kind)
        if not os.path.isdir(kdir):
            continue
        years = sorted((y for y in os.listdir(kdir) if os.path.isdir(os.path.join(kdir, y))), key=nk)
        counts = {}
        for y in years:
            ydir = os.path.join(kdir, y)
            files = [f for f in os.listdir(ydir) if f.endswith(".md") and f != "index.md"]
            counts[y] = len(files)
            if years_touched is not None and (kind, y) not in years_touched and os.path.exists(os.path.join(ydir, "index.md")):
                continue
            items = []
            for f in sorted(files, key=nk):
                t = re.search(r'^title: "(.*)"$', open(os.path.join(ydir, f), encoding="utf-8").read(2000), re.M)
                items.append(f"* [{t.group(1) if t else f}]({f})")
            open(os.path.join(ydir, "index.md"), "w", encoding="utf-8").write(
                f"# {KIND_TITLES.get(kind, kind)} — {y}\n\n" + "\n".join(items) + "\n")
        open(os.path.join(kdir, "index.md"), "w", encoding="utf-8").write(
            f"# {KIND_TITLES.get(kind, kind)}\n\n" + "\n".join(f"* [{y}]({y}/) - {counts[y]} documents" for y in reversed(years)) + "\n")
    kinds = sorted(k for k in os.listdir(root) if os.path.isdir(os.path.join(root, k)))
    open(os.path.join(root, "index.md"), "w", encoding="utf-8").write(
        "# Presidential Documents\n\nExecutive Orders, Proclamations, Memoranda, Notices and Determinations as published in the "
        "Federal Register (1994-present). Metadata from the Office of the Federal Register; official text from govinfo.\n\n" +
        "\n".join(f"* [{KIND_TITLES.get(k, k)}]({k}/)" for k in kinds) + "\n")
    exe = os.path.dirname(root)
    subs = sorted(k for k in os.listdir(exe) if os.path.isdir(os.path.join(exe, k)))
    names = {"presidential-documents": "Presidential Documents", "olc-opinions": "Office of Legal Counsel Opinions"}
    open(os.path.join(exe, "index.md"), "w", encoding="utf-8").write(
        "# Executive Branch\n\nPresidential actions and executive legal opinions. Agency rules live in "
        "[the CFR](/federal/cfr/) and [the Federal Register](/federal/federal-register/); agency adjudications in "
        "[agency decisions](/federal/agency/).\n\n" + "\n".join(f"* [{names.get(s, s)}]({s}/)" for s in subs) + "\n")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="legal")
    ap.add_argument("--from", dest="start", default="", help="YYYY-MM-DD (backfill start; API coverage starts 1994)")
    ap.add_argument("--since", default="", help="YYYY-MM-DD: only documents published on/after this date")
    ap.add_argument("--to", default="", help="YYYY-MM-DD (default today)")
    ap.add_argument("--force", action="store_true", help="rewrite documents already on disk")
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--split-chars", type=int, default=6000)
    ap.add_argument("--report", default="", help="append a JSON line per new/updated document to this file")
    a = ap.parse_args()
    start = dt.date.fromisoformat(a.since or a.start or "1994-01-01")
    end = dt.date.fromisoformat(a.to) if a.to else dt.date.today()
    run_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    root = os.path.join(a.out, "federal", "executive", "presidential-documents")
    stats, touched = Counter(), set()
    rep = open(a.report, "a", encoding="utf-8") if a.report else None
    for r in list_docs(start, end, a.delay):
        stats["listed"] += 1
        path = doc_path(root, r)
        if os.path.exists(path) and not a.force:
            stats["already_written"] += 1; continue
        try:
            paras = govinfo_text(r["publication_date"], r["document_number"])
        except Exception as e:
            stats["text_unavailable"] += 1
            print(f"  no text for {r['document_number']}: {e}", flush=True)
            continue
        if not paras:
            stats["empty"] += 1; continue
        write_doc(root, r, paras, run_at, a.split_chars)
        touched.add((kind_of(r), (r.get("signing_date") or r.get("publication_date") or "undated")[:4]))
        stats["written"] += 1; stats[kind_of(r)] += 1
        if rep:
            rep.write(json.dumps({"branch": "executive", "feed": "presidential-documents", "path": os.path.relpath(path, a.out),
                                  "title": re.sub(r"\s+", " ", f"{r.get('subtype')} {number_of(r)}").strip() + f" — {r.get('title')}"}) + "\n")
        if stats["written"] % 200 == 0:
            print(f"  {stats['written']:,} written ({r['publication_date']})", flush=True)
        time.sleep(a.delay)
    rebuild_indexes(root, touched if (a.since and os.path.exists(os.path.join(root, "index.md"))) else None)
    ref = os.path.join(a.out, "references"); os.makedirs(ref, exist_ok=True)
    json.dump({"from": start.isoformat(), "to": end.isoformat(), "stats": dict(stats)},
              open(os.path.join(ref, "ingest-stats-presidential-documents.json"), "w"), indent=1)
    print("done:", json.dumps(dict(stats)), flush=True)

if __name__ == "__main__":
    main()

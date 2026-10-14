#!/usr/bin/env python3
"""
fr_to_okf.py — Federal Register (govinfo bulk XML) -> OKF v0.2 concepts, one per document.

Input: govinfo.gov/bulkdata/FR monthly zips (FR-YYYY-MM.zip) or daily XMLs (FR-YYYY-MM-DD.xml),
in a directory. Use --fetch YYYY[-MM] to download months first (polite, cache-first).

Each RULE / PRORULE / NOTICE / PRESDOCU becomes
  <out>/federal/federal-register/<YYYY>/<MM>/<type>/<frdoc>.md
with agency, sub-agency, affected CFR parts (linked to the CFR layer), docket, RIN,
subject, summary, publication date and the FR citation (e.g. "91 FR 62297").
Long documents are split into parts. Stdlib only.
"""
import argparse, datetime as dt, hashlib, io, json, os, re, sys, time, urllib.request, zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uslm_to_okf as U
import ecfr_to_okf as E

TOOL = "fr_to_okf/1.0"
UA = "OKF-LawCorpus/1.0 (open legal-knowledge research; contact " + os.environ.get("LEGAL_US_CONTACT", "see github.com/MetatronAngelAgent/legal-united-states") + ")"
TYPES = {"RULE": ("rule", "Final Rule"), "PRORULE": ("proposed-rule", "Proposed Rule"),
         "NOTICE": ("notice", "Notice"), "PRESDOCU": ("presidential-document", "Presidential Document")}
E.BLOCK |= {"HD", "SECTNO", "AMDPAR", "SIG", "NAME", "TITLE", "GPH", "FURINF", "ADD", "DATES",
            "EFFDATE", "ACT", "AGY", "SUM", "LSTSUB", "PSIG", "PLACE", "DATE", "PRES", "EXECORDR",
            "PRMEMO", "PROCLA", "DETERM", "PRNOTICE", "PRORDER", "FP-1", "FP-2"}

def fetch(spec, dest):
    os.makedirs(dest, exist_ok=True)
    y, m = (spec.split("-") + [None])[:2]
    months = [f"{int(m):02d}"] if m else [f"{i:02d}" for i in range(1, 13)]
    for mm in months:
        fn = os.path.join(dest, f"FR-{y}-{mm}.zip")
        if os.path.exists(fn) and os.path.getsize(fn) > 1000:
            continue
        url = f"https://www.govinfo.gov/bulkdata/FR/{y}/{mm}/FR-{y}-{mm}.zip"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            data = urllib.request.urlopen(req, timeout=600).read()
            open(fn, "wb").write(data)
            print(f"  fetched {os.path.basename(fn)} ({len(data)/1e6:.1f} MB)", flush=True)
        except Exception as e:
            print(f"  skip {y}-{mm}: {e}", flush=True)
        time.sleep(2)

def issues(src):
    """Yield (name, bytes) for every daily issue XML under src (zips or loose XML)."""
    for f in sorted(os.listdir(src)):
        p = os.path.join(src, f)
        if f.endswith(".zip"):
            with zipfile.ZipFile(p) as z:
                for n in sorted(z.namelist()):
                    if re.search(r"FR-\d{4}-\d{2}-\d{2}\.xml$", n):
                        yield os.path.basename(n), z.read(n)
        elif re.match(r"FR-\d{4}-\d{2}-\d{2}\.xml$", f):
            yield f, open(p, "rb").read()

def txt(e):
    return U.normspace(" ".join(e.itertext())) if e is not None else ""

def cfr_links(cfr_text):
    """'14 CFR Part 39' / '40 CFR Parts 52 and 81' -> [(title, part)]"""
    out = []
    for m in re.finditer(r"(\d+)\s*CFR\s*Parts?\s*([\d,\sand\-–]+)", cfr_text or ""):
        t = m.group(1)
        for p in re.findall(r"\d+", m.group(2)):
            out.append((t, p))
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="dir of FR-YYYY-MM.zip / FR-YYYY-MM-DD.xml")
    ap.add_argument("--out", default="legal")
    ap.add_argument("--fetch", action="append", default=[], help="YYYY or YYYY-MM to download first")
    ap.add_argument("--types", default="RULE,PRORULE,NOTICE,PRESDOCU")
    ap.add_argument("--split-chars", type=int, default=6000)
    ap.add_argument("--report", default="", help="append a JSON line per document written")
    a = ap.parse_args()
    for spec in a.fetch:
        fetch(spec, a.src)
    want = {t.strip() for t in a.types.split(",")}
    run_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    root = os.path.join(a.out, "federal", "federal-register")
    stats, idx = defaultdict(int), defaultdict(list)

    for name, data in issues(a.src):
        m = re.search(r"FR-(\d{4})-(\d{2})-(\d{2})", name)
        y, mo, d = m.groups()
        pubdate = f"{y}-{mo}-{d}"
        try:
            r = ET.fromstring(data)
        except ET.ParseError as e:
            stats["bad_issues"] += 1; print(f"  parse error {name}: {e}"); continue
        vol = (r.findtext("VOL") or "").strip()
        page = None
        stats["issues"] += 1
        # walk in document order so the last-seen PRTPAGE gives each doc's start page
        for el in r.iter():
            if el.tag == "PRTPAGE" and re.fullmatch(r"\d+", el.get("P") or ""):
                page = el.get("P")            # numeric pages only (CNTNTS uses roman numerals)
            if el.tag not in TYPES or el.tag not in want:
                continue
            slugtype, label = TYPES[el.tag]
            pre = el.find("PREAMB") if el.tag != "PRESDOCU" else el
            # a document starts on the page marked at its very top, else on the last page
            # marker seen before it in reading order
            lead = pre[0] if (pre is not None and len(pre)) else None
            top = lead.get("P") if (lead is not None and lead.tag == "PRTPAGE"
                                    and re.fullmatch(r"\d+", lead.get("P") or "")) else None
            start_page = top or page
            frdoc_t = txt(el.find(".//FRDOC"))
            fm = re.search(r"FR Doc\.?\s*([\w\-]+)", frdoc_t)
            frdoc = fm.group(1) if fm else f"{pubdate}-{stats['issues']}-{stats['docs']}"
            agency = txt(pre.find("AGENCY")) if pre is not None else ""
            subagy = txt(pre.find("SUBAGY")) if pre is not None else ""
            subject = txt(pre.find("SUBJECT")) if pre is not None else ""
            if el.tag == "PRESDOCU":
                subject = subject or txt(el.find(".//HD")) or txt(el.find(".//TITLE3"))
                agency = agency or "Executive Office of the President"
            cfr = txt(pre.find("CFR")) if pre is not None else ""
            docket = txt(pre.find("DEPDOC")) if pre is not None else ""
            rin = txt(pre.find("RIN")) if pre is not None else ""
            summ = txt(pre.find("SUM")) if pre is not None else ""
            summ = re.sub(r"^SUMMARY:\s*", "", summ)
            paras = []
            for c in el:
                if c.tag in ("FRDOC", "BILCOD"):
                    continue
                E.render(c, paras)
            paras = [p for p in paras if p]
            body = "\n\n".join(paras)
            cite = f"{vol} FR {start_page}" if (vol and start_page) else f"FR Doc. {frdoc}"
            title = f"{cite} — {subject or label}"[:300]
            links = cfr_links(cfr)
            path = os.path.join(root, y, mo, slugtype, f"{U.slug(frdoc)}.md")
            pairs = [
                ("type", f"Federal Register {label}"),
                ("title", title),
                ("description", (summ or subject or label)[:240]),
                ("resource", f"https://www.federalregister.gov/d/{frdoc}"),
                ("tags", ["federal", "federal-register", slugtype] + ([U.slug(agency)[:60]] if agency else [])),
                ("citation", cite), ("fr_document_number", frdoc), ("publication_date", pubdate),
                ("agency", agency), ("sub_agency", subagy), ("affects_cfr", cfr),
                ("docket", docket), ("rin", rin),
                ("branch", "executive"),
                ("jurisdiction", "united-states"), ("jurisdiction_level", "federal"),
                ("status", "stable"),
                ("generated", {"by": TOOL, "at": run_at}),
                ("sources", ("raw", "\n  - { id: govinfo-fr, resource: " +
                             U.yq(f"https://www.govinfo.gov/bulkdata/FR/{y}/{mo}/FR-{pubdate}.xml") +
                             ", title: " + U.yq(f"Federal Register, Vol. {vol}, {pubdate}") +
                             ", author: " + U.yq("process:us-gpo-ofr") +
                             ", last_modified: " + U.yq(pubdate + "T00:00:00Z") + " }")),
                ("checksum", hashlib.sha256(body.encode()).hexdigest()),
            ]
            head = ""
            if summ:
                head += "# Summary\n\n" + summ + "\n\n"
            if links:
                head += "# Affected Regulations\n\n" + "\n".join(
                    f"- [{t} C.F.R. Part {p}](/federal/cfr/title-{t}/part-{p}/index.md)" for t, p in links) + "\n\n"
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if len(body) <= a.split_chars or len(paras) < 2:
                open(path, "w", encoding="utf-8").write(U.emit_frontmatter(pairs) + "\n" + head + "# Text\n\n" + body + "\n")
                stats["files"] += 1
            else:
                chunks, cur, sz = [], [], 0
                for p in paras:
                    if cur and sz + len(p) > a.split_chars:
                        chunks.append(cur); cur, sz = [], 0
                    cur.append(p); sz += len(p) + 2
                if cur:
                    chunks.append(cur)
                parts_dir = path[:-3]
                os.makedirs(parts_dir, exist_ok=True)
                lk = []
                for i, c in enumerate(chunks, 1):
                    pb = "\n\n".join(c)
                    pt = f"{title} (part {i} of {len(chunks)})"
                    pp = [(k, (pt if k == "title" else hashlib.sha256(pb.encode()).hexdigest() if k == "checksum" else v))
                          for k, v in pairs] + [("part_of", f"{i} of {len(chunks)}")]
                    fn = os.path.join(parts_dir, f"part-{i:03d}.md")
                    open(fn, "w", encoding="utf-8").write(U.emit_frontmatter(pp) + "\n# Text\n\n" + pb + "\n")
                    lk.append((f"{os.path.basename(parts_dir)}/part-{i:03d}.md", pt)); stats["files"] += 1
                with open(os.path.join(parts_dir, "index.md"), "w", encoding="utf-8") as f:
                    f.write(f"# {title}\n\n" + "\n".join(f"* [{t}]({os.path.basename(p)})" for p, t in lk) + "\n")
                open(path, "w", encoding="utf-8").write(
                    U.emit_frontmatter(pairs + [("parts", str(len(chunks)))]) + "\n" + head +
                    "# Parts\n\n" + "\n".join(f"- [{t}]({p})" for p, t in lk) + "\n")
                stats["files"] += 1; stats["split"] += 1
            idx[(y, mo, slugtype)].append((os.path.basename(path), title))
            stats["docs"] += 1; stats[slugtype] += 1
        print(f"  {name}: cumulative {stats['docs']} docs", flush=True) if stats["issues"] % 20 == 0 else None

    # indexes: root / year / month / type — rebuilt from what is on disk, so a daily run that
    # converts one issue keeps the rest of the month's documents in the index
    def ftitle(p):
        m = re.search(r'^title: "(.*)"$', open(p, encoding="utf-8").read(2000), re.M)
        return m.group(1) if m else os.path.basename(p)
    months_touched = set()
    for (y, mo, t) in idx:
        tdir = os.path.join(root, y, mo, t)
        items = sorted((f, ftitle(os.path.join(tdir, f))) for f in os.listdir(tdir) if f.endswith(".md") and f != "index.md")
        with open(os.path.join(tdir, "index.md"), "w", encoding="utf-8") as f:
            f.write(f"# Federal Register — {y}-{mo} — {t.replace('-', ' ').title()}s\n\n" +
                    "\n".join(f"* [{tt}]({p})" for p, tt in items) + "\n")
        months_touched.add((y, mo))
    for y, mo in months_touched:
        mdir = os.path.join(root, y, mo)
        types = {t: sum(1 for f in os.listdir(os.path.join(mdir, t)) if f.endswith(".md") and f != "index.md")
                 for t in os.listdir(mdir) if os.path.isdir(os.path.join(mdir, t))}
        open(os.path.join(mdir, "index.md"), "w").write(
            f"# Federal Register — {y}-{mo}\n\n" + "\n".join(
                f"* [{t.replace('-', ' ').title()}s]({t}/) - {n} documents" for t, n in sorted(types.items())) + "\n")
    for y in {y for y, _ in months_touched}:
        ydir = os.path.join(root, y)
        months = sorted(m for m in os.listdir(ydir) if os.path.isdir(os.path.join(ydir, m)))
        open(os.path.join(ydir, "index.md"), "w").write(f"# Federal Register — {y}\n\n" + "\n".join(f"* [{y}-{mo}]({mo}/)" for mo in months) + "\n")
    os.makedirs(root, exist_ok=True)
    years = sorted(d for d in os.listdir(root) if re.match(r"^\d{4}$", d))
    open(os.path.join(root, "index.md"), "w").write("# Federal Register\n\n" + "\n".join(f"* [{y}]({y}/)" for y in years) + "\n")
    ref = os.path.join(a.out, "references"); os.makedirs(ref, exist_ok=True)
    json.dump(dict(stats), open(os.path.join(ref, "ingest-stats-federal-register.json"), "w"), indent=1)
    if a.report:
        with open(a.report, "a", encoding="utf-8") as rf:
            for (y, mo, t), items in idx.items():
                for p, tt in items:
                    rf.write(json.dumps({"branch": "executive", "feed": "federal-register", "path": os.path.relpath(os.path.join(root, y, mo, t, p), a.out), "title": tt, "kind": t}) + "\n")
    print("done:", dict(stats), flush=True)

if __name__ == "__main__":
    main()

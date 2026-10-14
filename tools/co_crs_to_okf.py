#!/usr/bin/env python3
"""
co_crs_to_okf.py — Colorado Revised Statutes (official OLLS bulk HTML) -> OKF v0.2 concepts.

Source: Colorado Office of Legislative Legal Services, official annual CRS release, linked from
https://content.leg.colorado.gov/agencies/office-legislative-legal-services/<year>-crs-titles-download
as https://olls.info/crs/crs<year>-htm.zip (one Word-exported HTML file per title).
Statutory text and the OLLS annotations are public domain (Georgia v. Public.Resource.Org, 2020).
No third-party compilation license applies — commercial use is fine.

Each section's body is the LAST occurrence of its "18-3-102. Heading." paragraph in the title file
(earlier occurrences are the article tables of contents). Body runs to the next section start or
ARTICLE/PART heading and is split into Text / Source (history) / Cross references / Annotation.

Output: <out>/colorado/statutes/title-<T>/article-<A>/[part-<P>/]sec-<n>.md (+ indexes, report)
Usage:  python3 co_crs_to_okf.py --year 2026 --cache co_cache --out legal
"""
import argparse, datetime as dt, hashlib, html, json, os, re, sys, time, urllib.request, zipfile
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uslm_to_okf as U

TOOL = "co_crs_to_okf/1.0"
UA = "OKF-LawCorpus/1.0 (open legal-knowledge research; contact " + os.environ.get(
    "LEGAL_US_CONTACT", "see github.com/MetatronAngelAgent/legal-united-states") + ")"
SEC_RE = re.compile(r"^(\d+(?:\.\d+)?-\d+(?:\.\d+)?-\d+(?:\.\d+)?)\.\s+(.*)$")
ART_RE = re.compile(r"^ARTICLE\s+(\d+(?:\.\d+)?)\b\s*(.*)$")
PART_RE = re.compile(r"^PART\s+(\d+(?:\.\d+)?)\b\s*(.*)$")

def paragraphs(path):
    h = open(path, encoding="cp1252", errors="replace").read()
    h = re.sub(r"(?s)<style.*?</style>|<head.*?</head>", "", h)
    out = []
    for p in re.findall(r"(?s)<p[^>]*>(.*?)</p>", h):
        t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", p))).strip()
        if t:
            out.append(t)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=dt.date.today().year)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", default="legal")
    ap.add_argument("--split-chars", type=int, default=6000)
    a = ap.parse_args()
    os.makedirs(a.cache, exist_ok=True)
    zp = os.path.join(a.cache, f"crs{a.year}-htm.zip")
    url = f"https://olls.info/crs/crs{a.year}-htm.zip"
    if not os.path.exists(zp):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=1800) as r, open(zp + ".part", "wb") as f:
            f.write(r.read())
        os.replace(zp + ".part", zp)
    xdir = os.path.join(a.cache, f"htm{a.year}")
    with zipfile.ZipFile(zp) as z:
        z.extractall(xdir)
    run_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    root = os.path.join(a.out, "colorado", "statutes")
    report, total, idx = {}, 0, defaultdict(list)
    heads = {}
    files = sorted((f for f in os.listdir(os.path.join(xdir, "htm") if os.path.isdir(os.path.join(xdir, "htm")) else xdir)
                    if re.search(r"title-\d+(?:\.\d+)?\.htm", f)), key=lambda s: float(re.search(r"title-(\d+(?:\.\d+)?)\.htm", s).group(1)))
    hdir = os.path.join(xdir, "htm") if os.path.isdir(os.path.join(xdir, "htm")) else xdir
    for fn in files:
        tnum = re.search(r"title-(\d+(?:\.\d+)?)\.htm", fn).group(1).lstrip("0") or "0"
        P = paragraphs(os.path.join(hdir, fn))
        ttitle = re.sub(r"\s*-\s*Colorado Revised Statutes.*$", "", next((p for p in P[:5] if p.upper().startswith("TITLE")), f"Title {tnum}"))
        last = {}
        for i, p in enumerate(P):
            m = SEC_RE.match(p)
            if m and m.group(1).split("-")[0] == tnum:
                last[m.group(1)] = i
        starts = sorted(last.values())
        start_set = set(starts)
        art = part = None
        art_name = part_name = ""
        written = 0
        bound = {}                                   # paragraph index -> (article, part) context
        for i, p in enumerate(P):
            ma, mp = ART_RE.match(p), PART_RE.match(p)
            if ma:
                art, part = ma.group(1), None
                art_name = ma.group(2) or (P[i + 1] if i + 1 < len(P) else "")
                heads[(tnum, art)] = art_name
            elif mp:
                part, part_name = mp.group(1), mp.group(2)
                heads[(tnum, art, part)] = part_name
            if i in start_set:
                bound[i] = (art, part)
        for si, i in enumerate(starts):
            num, rest = SEC_RE.match(P[i]).groups()
            j = starts[si + 1] if si + 1 < len(starts) else len(P)
            block = []
            for p in P[i + 1:j]:
                if ART_RE.match(p) or PART_RE.match(p):
                    break
                block.append(p)
            # heading = rest up to first sentence end; any trailing text on the header line is body
            hm = re.match(r"^(.*?\.)(\s+.*)?$", rest)
            heading = (hm.group(1) if hm else rest).strip()
            first = (hm.group(2) or "").strip() if hm else ""
            text, source, xref, annot, mode = ([first] if first else []), [], [], [], "text"
            for p in block:
                if p.startswith("Source:"):
                    mode = "source"
                elif p.startswith("Cross references:") or p.startswith("Editor's note:"):
                    mode = "xref"
                elif p == "ANNOTATION" or p.startswith("ANNOTATION"):
                    mode = "annot"; continue
                {"text": text, "source": source, "xref": xref, "annot": annot}[mode].append(p)
            art_, part_ = bound.get(i, (None, None))
            ddir = os.path.join(root, f"title-{U.slug(tnum)}", *([f"article-{U.slug(art_)}"] if art_ else []),
                                *([f"part-{U.slug(part_)}"] if part_ else []))
            path = os.path.join(ddir, f"sec-{U.slug(num)}.md")
            repealed = bool(re.search(r"\b(Repealed|Reserved)\b", heading)) and len(" ".join(text)) < 300
            body = "\n\n".join(text) or "(No text.)"
            cite = f"C.R.S. § {num}"
            pairs = [
                ("type", "Statute Section"), ("title", f"{cite} — {heading.rstrip('.')}"),
                ("description", heading.rstrip(".")), ("resource", url + f" ({fn})"),
                ("tags", ["colorado", "statutes", "state-law", f"crs-title-{tnum}"]),
                ("citation", cite), ("jurisdiction", "colorado"), ("jurisdiction_level", "state"),
                ("hierarchy", " / ".join(x for x in (ttitle, f"Article {art_}" if art_ else "", f"Part {part_}" if part_ else "") if x)),
                ("edition", f"Colorado Revised Statutes {a.year}"),
                ("status", "deprecated" if repealed else "stable"),
                ("license", "Public domain (official OLLS publication; no compilation license)"),
                ("generated", {"by": TOOL, "at": run_at}),
                ("sources", ("raw", "\n  - { id: olls, resource: " + U.yq(url) +
                             ", title: " + U.yq(f"Colorado Revised Statutes {a.year} — official OLLS HTML release") +
                             ", author: " + U.yq("process:colorado-olls") + " }")),
                ("checksum", hashlib.sha256(body.encode()).hexdigest()),
            ]
            extra = ""
            if source:
                extra += "\n# History\n\n" + "\n\n".join(source) + "\n"
            if xref:
                extra += "\n# Cross-references\n\n" + "\n\n".join(xref) + "\n"
            if annot:
                extra += "\n# Annotations\n\n" + "\n\n".join(annot) + "\n"
            os.makedirs(ddir, exist_ok=True)
            md = U.emit_frontmatter(pairs) + "\n# Text\n\n" + body + "\n" + extra
            if len(md) > a.split_chars * 3 and annot:
                # very long annotations: move them to a companion concept to keep chunks focused
                apath = path[:-3] + "--annotations.md"
                ap_pairs = [(k, (f"{cite} — Annotations" if k == "title" else "Annotation" if k == "type" else
                                 hashlib.sha256("\n".join(annot).encode()).hexdigest() if k == "checksum" else v))
                            for k, v in pairs]
                open(apath, "w", encoding="utf-8").write(U.emit_frontmatter(ap_pairs) + "\n# Annotations\n\n" + "\n\n".join(annot) + "\n")
                extra = extra.split("\n# Annotations")[0] + f"\n# Annotations\n\nSee [{cite} — Annotations]({os.path.basename(apath)}).\n"
                md = U.emit_frontmatter(pairs) + "\n# Text\n\n" + body + "\n" + extra
            open(path, "w", encoding="utf-8").write(md)
            idx[ddir].append((os.path.basename(path), f"{cite} — {heading.rstrip('.')}"))
            written += 1
        report[tnum] = {"sections_found": len(starts), "written": written, "title": ttitle}
        total += written
        print(f"title {tnum}: {written} sections", flush=True)

    def nk(s):
        return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s)]
    alldirs = set(idx)
    for d in list(alldirs):
        while d != root and len(d) > len(root):
            d = os.path.dirname(d); alldirs.add(d)
    alldirs.add(root)
    for d in sorted(alldirs):
        subs = sorted((x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x))), key=nk)
        items = sorted(idx.get(d, []), key=lambda t: nk(t[0]))
        name = os.path.relpath(d, os.path.join(a.out, "colorado")).replace(os.sep, " / ")
        open(os.path.join(d, "index.md"), "w", encoding="utf-8").write(
            f"# Colorado — {name}\n\n" + "\n".join([f"* [{s}]({s}/)" for s in subs] + [f"* [{t}]({f})" for f, t in items]) + "\n")
    cdir = os.path.join(a.out, "colorado")
    kinds = sorted(x for x in os.listdir(cdir) if os.path.isdir(os.path.join(cdir, x)))
    open(os.path.join(cdir, "index.md"), "w", encoding="utf-8").write(
        "# Colorado\n\n" + "\n".join(f"* [{k.replace('-', ' ').title()}]({k}/)" for k in kinds) + "\n")
    ref = os.path.join(a.out, "references"); os.makedirs(ref, exist_ok=True)
    json.dump({"year": a.year, "total": total, "titles": report}, open(os.path.join(ref, "ingest-stats-colorado.json"), "w"), indent=1)
    print(f"done: {total} sections across {len(report)} titles", flush=True)

if __name__ == "__main__":
    main()

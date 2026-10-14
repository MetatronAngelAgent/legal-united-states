#!/usr/bin/env python3
"""
ecfr_to_okf.py — Code of Federal Regulations (govinfo eCFR bulk XML) -> OKF v0.2 concepts.

Input: a directory of title-<n>.xml files from ecfr_fetch.py (govinfo.gov/bulkdata/ECFR).
Hierarchy in the XML: DIV1 TITLE / DIV2 SUBTITLE / DIV3 CHAPTER / DIV4 SUBCHAP / DIV5 PART /
DIV6 SUBPART / DIV7 SUBJGRP / DIV8 SECTION / DIV9 APPENDIX.

Output: <out>/federal/cfr/title-<T>/part-<P>/sec-<N>.md (+ appendix-*.md), one concept per
section, with chapter/subchapter/subpart in frontmatter, the part's Authority and Source
notes carried onto each section, index.md per title and part, and a validation report.
Streams with iterparse, so 200 MB titles are fine. Stdlib only.
"""
import argparse, datetime as dt, hashlib, json, os, re, sys
import xml.etree.ElementTree as ET
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uslm_to_okf as U

TOOL = "ecfr_to_okf/1.0"
LEVEL = {"DIV1": "title", "DIV2": "subtitle", "DIV3": "chapter", "DIV4": "subchapter",
         "DIV5": "part", "DIV6": "subpart", "DIV7": "subjgrp", "DIV8": "section", "DIV9": "appendix"}
BLOCK = {"P", "FP", "HD1", "HD2", "HD3", "NOTE", "EXTRACT", "EXAMPLE", "FTNT", "EDNOTE",
         "SECAUTH", "APPRO", "PSPACE", "STARS", "FRP", "AMDPAR", "LI"}

def text_of(el):
    """Inline text of an element (emphasis/superscripts flattened)."""
    return U.normspace("".join(el.itertext()))

def render(el, out):
    """Append markdown paragraphs for an element subtree."""
    tag = el.tag
    if tag in ("HEAD", "CITA", "AUTH", "SOURCE", "PRTPAGE"):
        return
    if tag == "TABLE":                       # govinfo eCFR also emits plain HTML tables
        rows = []
        for i, tr in enumerate(el.iter("TR")):
            cells = [text_of(c) for c in tr if c.tag in ("TH", "TD")]
            if any(cells):
                rows.append("| " + " | ".join(cells) + " |")
                if i == 0 and any(c.tag == "TH" for c in tr):
                    rows.append("|" + "---|" * len(cells))
        if rows:
            out.append("\n".join(rows))
        return
    if tag == "GPOTABLE":
        rows = []
        for row in el.iter("ROW"):
            cells = [text_of(c) for c in row]
            if any(cells):
                rows.append("| " + " | ".join(cells) + " |")
        heads = [text_of(h) for h in el.iter("CHED")]
        if heads:
            rows.insert(0, "| " + " | ".join(heads) + " |")
            rows.insert(1, "|" + "---|" * len(heads))
        ttl = el.find("TTITLE")
        if ttl is not None and text_of(ttl):
            out.append(f"**{text_of(ttl)}**")
        if rows:
            out.append("\n".join(rows))
        return
    if tag in BLOCK and not any(c.tag in BLOCK or c.tag == "GPOTABLE" for c in el):
        t = text_of(el)
        if t:
            out.append(f"**{t}**" if tag.startswith("HD") else t)
        return
    if el.text and el.text.strip() and (tag in BLOCK or tag in ("DIV", "SPAN", "LI")):
        out.append(U.normspace(el.text))
    if tag not in BLOCK and tag not in ("DIV", "SPAN") and len(el) == 0 and el.text and el.text.strip() \
            and tag not in ("XREF", "E", "SU", "FR", "AC", "I", "B"):
        out.append(U.normspace(el.text)); return
    for c in el:
        render(c, out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", default="legal")
    ap.add_argument("--titles", default="")
    ap.add_argument("--split-chars", type=int, default=6000)
    a = ap.parse_args()
    run_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).strftime("%Y-%m-%dT00:00:00Z")
    meta = {t["number"]: t for t in json.load(open(os.path.join(a.src, "titles.json")))["titles"]}
    want = {int(x) for x in a.titles.split(",") if x.strip()}
    root = os.path.join(a.out, "federal", "cfr")
    stats, gaps = defaultdict(int), []
    title_idx = []

    for n in sorted(meta):
        fn = os.path.join(a.src, f"title-{n}.xml")
        if (want and n not in want) or not os.path.exists(fn):
            if not meta[n].get("reserved") and not want:
                gaps.append(f"title {n}: not downloaded")
            continue
        asof = meta[n]["up_to_date_as_of"]
        tname = meta[n]["name"]
        tdir = os.path.join(root, f"title-{n}")
        ctx = {}                                 # level -> (num, heading)
        part_notes = {}                          # part -> (authority, source)
        part_idx = defaultdict(list)
        part_heads = {}
        seen = set()
        n_sec = 0
        for ev, el in ET.iterparse(fn, events=("start", "end")):
            lv = LEVEL.get(el.tag)
            if ev == "start":
                if lv and lv not in ("section", "appendix"):
                    ctx[lv] = (el.get("N", ""), None)
                    for deeper in list(LEVEL.values())[list(LEVEL.values()).index(lv) + 1:]:
                        ctx.pop(deeper, None)
                continue
            # end events
            if lv and lv not in ("section", "appendix"):
                h = el.find("HEAD")
                if lv == "part":
                    pnum = el.get("N", "")
                    part_heads[pnum] = text_of(h) if h is not None else f"Part {pnum}"
                el.clear()
                continue
            if lv in ("section", "appendix"):
                num = re.sub(r"^[§\s]+", "", el.get("N", "")).strip()   # govinfo N carries "§ "
                head = el.find("HEAD")
                heading = text_of(head) if head is not None else ""
                heading = re.sub(r"^§+\s*[\d.\-A-Za-z]+\s*", "", heading).strip() if lv == "section" else heading
                paras = []
                for c in el:
                    render(c, paras)
                cita = el.find("CITA")
                hist = text_of(cita) if cita is not None else ""
                part = ctx.get("part", ("", None))[0]
                if not part:
                    m = re.match(r"^(\d+[A-Za-z]?)\.", num)
                    part = m.group(1) if m else "misc"
                pdir = os.path.join(tdir, f"part-{U.slug(part)}")
                base = f"sec-{U.slug(num)}" if lv == "section" else f"appendix-{U.slug(num or heading)[:60]}"
                path = os.path.join(pdir, base + ".md")
                k = 2
                while path in seen:
                    path = os.path.join(pdir, f"{base}--v{k}.md"); k += 1
                seen.add(path)
                cite = f"{n} C.F.R. § {num}" if lv == "section" else f"{n} C.F.R. pt. {part}, {heading[:80]}"
                title = f"{cite} — {heading}" if (heading and lv == "section") else cite
                reserved = bool(re.search(r"\[Reserved|\bRemoved\b", heading, re.I)) and not paras
                heading = re.sub(r"\[Reserved$", "[Reserved]", heading)
                body = "\n\n".join(paras) or ("(Reserved.)" if reserved else "")
                if not paras and not reserved:
                    gaps.append(f"heading only in source (no text): {cite}"); stats["empty"] += 1
                url = (f"https://www.ecfr.gov/current/title-{n}/section-{num}" if lv == "section"
                       else f"https://www.ecfr.gov/current/title-{n}/part-{part}")
                pairs = [
                    ("type", "Regulation Section" if lv == "section" else "Regulation Appendix"),
                    ("title", title), ("description", heading or cite), ("resource", url),
                    ("tags", ["federal", "cfr", f"cfr-title-{n}"]),
                    ("citation", cite),
                    ("cfr_title", f"{n} — {tname}"),
                    ("chapter", ctx.get("chapter", ("", None))[0]),
                    ("subchapter", ctx.get("subchapter", ("", None))[0]),
                    ("part", part), ("subpart", ctx.get("subpart", ("", None))[0]),
                    ("jurisdiction", "united-states"), ("jurisdiction_level", "federal"),
                    ("current_as_of", asof),
                    ("status", "deprecated" if reserved else "stable"),
                    ("stale_after", stale),
                    ("generated", {"by": TOOL, "at": run_at}),
                    ("sources", ("raw", "\n  - { id: govinfo-ecfr, resource: " +
                                 U.yq(f"https://www.govinfo.gov/bulkdata/ECFR/title-{n}/ECFR-title{n}.xml") +
                                 ", title: " + U.yq(f"eCFR Title {n} bulk XML (current as of {asof})") +
                                 ", author: " + U.yq("process:us-gpo-ofr") +
                                 ", last_modified: " + U.yq(asof + "T00:00:00Z") + " }")),
                    ("checksum", hashlib.sha256(body.encode()).hexdigest()),
                ]
                chunks = [paras]
                if len(body) > a.split_chars and len(paras) > 1:
                    chunks, cur, sz = [], [], 0
                    for p in paras:
                        if cur and sz + len(p) > a.split_chars:
                            chunks.append(cur); cur, sz = [], 0
                        cur.append(p); sz += len(p) + 2
                    if cur:
                        chunks.append(cur)
                os.makedirs(pdir, exist_ok=True)
                if len(chunks) == 1:
                    md = U.emit_frontmatter(pairs) + "\n# Text\n\n" + body + "\n"
                    if hist:
                        md += "\n# History\n\n" + hist + "\n"
                    open(path, "w", encoding="utf-8").write(md)
                    stats["files"] += 1
                else:
                    links = []
                    for i, c in enumerate(chunks, 1):
                        pp = path[:-3] + f"--part-{i}.md"
                        pt = f"{title} (part {i} of {len(chunks)})"
                        pb = "\n\n".join(c)
                        pp_pairs = [(k2, (pt if k2 == "title" else hashlib.sha256(pb.encode()).hexdigest()
                                          if k2 == "checksum" else v)) for k2, v in pairs] + [("part_of", f"{i} of {len(chunks)}")]
                        open(pp, "w", encoding="utf-8").write(U.emit_frontmatter(pp_pairs) + "\n# Text\n\n" + pb + "\n")
                        links.append((os.path.basename(pp), pt)); stats["files"] += 1
                    md = U.emit_frontmatter(pairs + [("parts", str(len(chunks)))]) + "\n# Text\n\n" + \
                         paras[0] + "\n\n# Parts\n\n" + "\n".join(f"- [{t}]({p})" for p, t in links) + "\n"
                    if hist:
                        md += "\n# History\n\n" + hist + "\n"
                    open(path, "w", encoding="utf-8").write(md)
                    stats["files"] += 1; stats["split"] += 1
                part_idx[part].append((os.path.basename(path), title))
                stats["sections" if lv == "section" else "appendices"] += 1
                n_sec += 1
                el.clear()
        # indexes
        def nk(s):
            return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s)]
        lines = [f"# Title {n} — {tname}", "", f"Current as of {asof}.", ""]
        for part in sorted(part_idx, key=nk):
            pdir = os.path.join(tdir, f"part-{U.slug(part)}")
            ph = part_heads.get(part, f"Part {part}")
            lines.append(f"* [{ph}](part-{U.slug(part)}/) - {len(part_idx[part])} sections")
            with open(os.path.join(pdir, "index.md"), "w", encoding="utf-8") as f:
                f.write(f"# {n} C.F.R. {ph}\n\n" + "\n".join(f"* [{t}]({p})" for p, t in
                        sorted(part_idx[part], key=lambda x: nk(x[0]))) + "\n")
        os.makedirs(tdir, exist_ok=True)
        open(os.path.join(tdir, "index.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
        title_idx.append((n, tname, n_sec))
        stats[f"t{n}"] = n_sec
        print(f"title {n}: {n_sec} sections/appendices", flush=True)

    ref = os.path.join(a.out, "references"); os.makedirs(ref, exist_ok=True)
    sp = os.path.join(ref, "ingest-stats-cfr.json")
    if want and os.path.exists(sp):
        # partial (daily) run: keep the other titles in the root index and the stats
        prev = json.load(open(sp))
        done_now = {n for n, _, _ in title_idx}
        for k, v in prev.items():
            if re.fullmatch(r"t\d+", k) and int(k[1:]) not in done_now:
                stats[k] = v
        title_idx = [(n, meta[n]["name"], stats.get(f"t{n}", 0)) for n in sorted(meta)
                     if os.path.isdir(os.path.join(root, f"title-{n}"))]
    with open(os.path.join(root, "index.md"), "w", encoding="utf-8") as f:
        f.write("# Code of Federal Regulations\n\n" +
                "\n".join(f"* [Title {n} — {nm}](title-{n}/) - {c} sections" for n, nm, c in title_idx) + "\n")
    rep = ["# Gaps report — Code of Federal Regulations", "", f"Run: {run_at}", "",
           f"- Titles: {len(title_idx)}", f"- Sections: {stats['sections']}",
           f"- Appendices: {stats['appendices']}", f"- Split sections: {stats['split']}",
           f"- Empty bodies: {stats['empty']}", ""]
    if gaps:
        rep += ["## Issues"] + [f"- {g}" for g in gaps]
    if not want:
        open(os.path.join(ref, "gaps-cfr.md"), "w", encoding="utf-8").write("\n".join(rep) + "\n")
    json.dump(dict(stats), open(sp, "w"), indent=1)
    print(f"done: {stats['sections']} sections, {stats['appendices']} appendices, "
          f"{stats['files']} files, {stats['empty']} empty", flush=True)

if __name__ == "__main__":
    main()

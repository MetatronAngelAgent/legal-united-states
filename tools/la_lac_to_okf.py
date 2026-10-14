#!/usr/bin/env python3
"""
la_lac_to_okf.py — Louisiana Administrative Code (official, Office of the State Register) -> OKF v0.2.

Source: https://www.doa.la.gov/doa/osr/louisiana-administrative-code/ — one Word (.docx) file per
title or title part, the state-certified publication. Paragraph styles carry the structure
(Title1, Part, Subpart, Chapter, Subchapter, Section, A./1./a./i. levels, Authority Note,
Historical Note), so sections are cut exactly.

Output: <out>/louisiana/regulations/title-T/[part-P/][subpart-S/]chapter-C/sec-N.md
Citation form: LAC T:P.N (e.g. LAC 1:I.101).
Usage:  python3 la_lac_to_okf.py --cache la_cache --out legal
"""
import argparse, html, os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import regs_common as R
import uslm_to_okf as U
try:
    import docx
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "python-docx"])
    import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

INDEX = "https://www.doa.la.gov/doa/osr/louisiana-administrative-code/"
LEVEL = {"A.": 0, "1.": 1, "a.": 2, "i.": 3, "(1)": 4, "(a)": 5, "Reg Double Indent": 1}

def blocks(doc):
    for el in doc.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            yield Paragraph(el, doc)
        elif tag == "tbl":
            yield Table(el, doc)

def table_md(t):
    rows = []
    for r in t.rows:
        cells, last = [], None
        for c in r.cells:
            if c._tc is last:            # merged cells repeat
                continue
            last = c._tc
            cells.append(U.normspace(c.text).replace("|", "\\|"))
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    w = max(len(r) for r in rows)
    rows = [r + [""] * (w - len(r)) for r in rows]
    return "\n".join(["| " + " | ".join(rows[0]) + " |", "|" + "---|" * w] + ["| " + " | ".join(r) + " |" for r in rows[1:]])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", default="legal")
    a = ap.parse_args()
    os.makedirs(a.cache, exist_ok=True)
    ip = R.fetch(INDEX, os.path.join(a.cache, "index.html"), delay=0)
    page = open(ip, encoding="utf-8", errors="replace").read()
    links = []
    for m in re.finditer(r'href="(/media/[^"]+\.docx)"[^>]*>([^<]*)', page):
        if m.group(1) not in [l[0] for l in links]:
            links.append((m.group(1), html.unescape(m.group(2)).strip()))
    print(f"{len(links)} docx files listed", flush=True)
    W = R.Writer(a.out, "louisiana", "Louisiana", "la_lac_to_okf/1.0", "la-osr",
                 "Louisiana Administrative Code — Office of the State Register (official .docx)", INDEX,
                 "Public domain (state government publication)", "Louisiana Administrative Code, as posted by the Office of the State Register")
    files_ok, sections = 0, 0
    for href, label in links:
        fn = os.path.join(a.cache, href.strip("/").replace("/", "_"))
        if not R.fetch("https://www.doa.la.gov" + href, fn, delay=2.0):
            continue
        try:
            doc = docx.Document(fn)
        except Exception as e:
            print(f"  unreadable {href}: {e}", flush=True); continue
        files_ok += 1
        title = re.search(r"Title\s+(\d+)", label)
        title = title.group(1) if title else "0"
        ctx = {"part": None, "subpart": None, "chapter": None, "subchapter": None}
        names = {}
        cur = None

        def flush():
            nonlocal cur, sections
            if not cur:
                return
            live = [(k, v) for k, v in cur["ctx"] if v]
            dirs = [f"title-{title}"] + [f"{k}-{U.slug(v)}" for k, v in live]
            part = dict(cur["ctx"]).get("part")
            cite = f"LAC {title}:{part + '.' if part else ''}{cur['num']}"
            hier = " / ".join([f"Title {title} {cur['tname']}".strip()] + [names.get((k, v), f"{k.title()} {v}") for k, v in live])
            W.write(dirs, f"sec-{U.slug(cur['num'])}", cite, cur["head"], "\n\n".join(cur["text"]),
                    INDEX + f" ({os.path.basename(href)})", hier, history="\n\n".join(cur["hist"]))
            for i, (k, v) in enumerate(live):
                W.name_dir(os.path.join(W.root, *dirs[:i + 2]), names.get((k, v), f"{k.title()} {v}"))
            W.name_dir(os.path.join(W.root, dirs[0]), f"Title {title} — {cur['tname']}")
            sections += 1
            cur = None

        tname = ""
        for b in blocks(doc):
            if isinstance(b, Table):
                if cur:
                    t = table_md(b)
                    if t:
                        (cur["hist"] if cur["in_hist"] else cur["text"]).append(t)
                continue
            st, txt = b.style.name if b.style is not None else "", U.normspace(b.text.replace("\t", " ").replace("\n", " "))
            if not txt or re.match(r"(?i)^toc \d", st):
                continue
            if st == "Title2":
                tname = txt.title() if txt.isupper() else txt
                continue
            m = re.match(r"^(Part|Subpart|Chapter|Subchapter)\s+([0-9A-Za-z.\-]+)\.?\s*(.*)$", txt)
            if st in ("Part", "Subpart", "Chapter", "Subchapter", "TOCPart") and m:
                flush()
                lvl = m.group(1).lower()
                v = m.group(2).rstrip(".")
                order = ["part", "subpart", "chapter", "subchapter"]
                ctx[lvl] = v
                for lower in order[order.index(lvl) + 1:]:
                    ctx[lower] = None
                names[(lvl, v)] = f"{m.group(1)} {v}. {m.group(3)}".strip()
                continue
            ms = re.match(r"^§\s*([0-9]+(?:\.[0-9]+)?[A-Z]?)\.?\s*(.*)$", txt)
            if st == "Section" or (ms and st in ("Normal", "") and len(txt) < 250):
                if ms:
                    flush()
                    cur = {"num": ms.group(1), "head": ms.group(2), "text": [], "hist": [], "in_hist": False,
                           "ctx": [(k, ctx[k]) for k in ("part", "subpart", "chapter", "subchapter")], "tname": tname}
                    continue
            if not cur:
                continue
            if st in ("Authority Note", "Historical Note") or re.match(r"^(AUTHORITY|HISTORICAL) NOTE:", txt):
                cur["in_hist"] = True
                cur["hist"].append(txt)
                continue
            if cur["in_hist"]:
                cur["hist"].append(txt)
                continue
            cur["text"].append("  " * LEVEL.get(st, 0) + txt)
        flush()
        print(f"  {label[:60]}: total sections {sections}", flush=True)
    W.finish("Official Louisiana Administrative Code from the Office of the State Register's Word files.",
             {"files_listed": len(links), "files_read": files_ok, "sections": sections})

if __name__ == "__main__":
    main()

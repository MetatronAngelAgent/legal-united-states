#!/usr/bin/env python3
"""
ri_ricr_to_okf.py — Rhode Island Code of Regulations (official, Secretary of State) -> OKF v0.2.

Source: https://rules.sos.ri.gov/Organizations — Title > Chapter > Subchapter > Part pages; each
active Part page embeds the official regulation text (converted from the filed Word document)
plus a link to the official PDF. Sections are cut at RICRHeading1 paragraphs ("1.1 Purpose").

Output: <out>/rhode-island/regulations/title-T/chapter-C/subchapter-S/part-P/sec-<P>-<n>.md
Citation form: 250-RICR-10-00-1.1
Usage:  python3 ri_ricr_to_okf.py --cache ri_cache --out legal
"""
import argparse, html, os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import regs_common as R
import uslm_to_okf as U
try:
    from bs4 import BeautifulSoup
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "beautifulsoup4", "lxml"])
    from bs4 import BeautifulSoup

BASE = "https://rules.sos.ri.gov"

def page(url, cache, name):
    p = R.fetch(url, os.path.join(cache, name), delay=2.0)
    return open(p, encoding="utf-8", errors="replace").read() if p else ""

def table_md(t):
    rows = [[U.normspace(c.get_text(" ")).replace("|", "\\|") for c in tr.find_all(["td", "th"])] for tr in t.find_all("tr")]
    rows = [r for r in rows if any(r)]
    if not rows:
        return ""
    w = max(len(r) for r in rows)
    rows = [r + [""] * (w - len(r)) for r in rows]
    return "\n".join(["| " + " | ".join(rows[0]) + " |", "|" + "---|" * w] + ["| " + " | ".join(r) + " |" for r in rows[1:]])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", default="legal")
    ap.add_argument("--titles", default="")
    a = ap.parse_args()
    want = {x.strip() for x in a.titles.split(",") if x.strip()}
    org = page(BASE + "/Organizations", a.cache, "organizations.html")
    titles = {}
    for m in re.finditer(r'href="https://rules\.sos\.ri\.gov/Organizations/Title/(\d+)">.*?</a>\s*</td>\s*<td[^>]*>([^<]+)</td>', org, re.S):
        titles.setdefault(m.group(1), html.unescape(m.group(2)).strip())
    containers = []
    for m in re.finditer(r'href="https://rules\.sos\.ri\.gov/Organizations/(SubChapter|Chapter)/([\w\-]+)"', org):
        if (m.group(1), m.group(2)) not in containers:
            containers.append((m.group(1), m.group(2)))
    W = R.Writer(a.out, "rhode-island", "Rhode Island", "ri_ricr_to_okf/1.0", "ri-sos",
                 "Rhode Island Code of Regulations — Secretary of State", BASE + "/Organizations",
                 "Public domain (state government publication)", "Rhode Island Code of Regulations (RICR), active rules")
    stats = {"containers": 0, "parts": 0, "parts_inactive_or_empty": 0, "sections": 0}
    parts = []
    for kind, cid in containers:
        if want and cid.split("-")[0] not in want:
            continue
        s = page(f"{BASE}/Organizations/{kind}/{cid}", os.path.join(a.cache, "nav"), f"{kind}-{cid}.html")
        stats["containers"] += 1
        for m in re.finditer(r'href="https://rules\.sos\.ri\.gov/regulations/Part/([\w\-]+)"', s):
            if m.group(1) not in parts:
                parts.append(m.group(1))
    print(f"{len(parts)} parts", flush=True)
    for pid in parts:
        s = page(f"{BASE}/regulations/Part/{pid}", os.path.join(a.cache, "parts"), f"{pid}.html")
        if not s:
            continue
        soup = BeautifulSoup(s, "lxml")
        bits = pid.split("-")
        if len(bits) < 4:
            continue
        t, ch, sc, pn = bits[0], bits[1], bits[2], "-".join(bits[3:])
        rtitle = U.normspace((soup.find("title").get_text() if soup.find("title") else pid).split(" - Rhode Island")[0])
        pname = re.sub(r"\s*\(\d+-RICR-[\w\-]+\)\s*$", "", rtitle)
        active = "ACTIVE RULE" in s
        eff = re.search(r"Effective\s*</?[^>]*>?\s*(\d{2}/\d{2}/\d{4})", s) or re.search(r"Effective\s+(\d{2}/\d{2}/\d{4})", re.sub(r"<[^>]+>", " ", s))
        pdf = re.search(r'href="(https://risos-apa-production-public\.s3\.amazonaws\.com/[^"]+\.pdf)"', s)
        body_paras = [p for p in soup.find_all(["p", "table"]) if (p.name == "table" and p.find_parent("table") is None and p.find(class_=re.compile("^pt-")))
                      or (p.name == "p" and any(c.startswith("pt-RICR") for c in (p.get("class") or [])) and p.find_parent("table") is None)]
        if not active or not body_paras:
            stats["parts_inactive_or_empty"] += 1; continue
        stats["parts"] += 1
        sections, cur = [], None
        for el in body_paras:
            if el.name == "table":
                if cur is not None:
                    tm = table_md(el)
                    if tm: cur["text"].append(tm)
                continue
            cls = next(c for c in el["class"] if c.startswith("pt-RICR"))
            txt = U.normspace(el.get_text(" "))
            if not txt or cls in ("pt-RICRNumber", "pt-RICRTitleNumber", "pt-RICRChapterNumber", "pt-RICRSubheadingSubchapter", "pt-RICRPart"):
                continue
            if cls == "pt-RICRHeading1":
                m = re.match(r"^(\d+\.\d+[A-Za-z]?)\s*(.*)$", txt)
                cur = {"num": m.group(1) if m else str(len(sections) + 1), "head": (m.group(2) if m else txt), "text": []}
                sections.append(cur); continue
            if cur is None:
                cur = {"num": pn, "head": pname, "text": []}; sections.append(cur)
            lvl = re.search(r"Paragraph(\d)", cls)
            ind = "  " * (int(lvl.group(1)) - 1) if lvl else ""
            cur["text"].append(ind + txt)
        dirs = [f"title-{t}", f"chapter-{ch}", f"subchapter-{sc}", f"part-{U.slug(pn)}"]
        W.name_dir(os.path.join(W.root, f"title-{t}"), f"Title {t} — {titles.get(t, '')}".rstrip(" —"))
        W.name_dir(os.path.join(W.root, *dirs), f"Part {pn} — {pname} ({t}-RICR-{ch}-{sc}-{pn})")
        for sec in sections:
            if not sec["text"] and re.match(r"^(Figure|Table|Exhibit|Appendix|Map|Diagram)\b", sec["head"]) and pdf:
                sec["text"] = [f"(Image or figure only; see the official PDF: {pdf.group(1)})"]
            cite = f"{t}-RICR-{ch}-{sc}-{sec['num']}" if "." in sec["num"] else f"{t}-RICR-{ch}-{sc}-{pn}"
            W.write(dirs, f"sec-{U.slug(sec['num'])}", cite, sec["head"], "\n\n".join(sec["text"]),
                    f"{BASE}/regulations/Part/{pid}", f"Title {t} {titles.get(t, '')} / Chapter {ch} / Subchapter {sc} / Part {pn} {pname}",
                    extra={"effective_date": eff.group(1) if eff else "", "official_pdf": pdf.group(1) if pdf else ""})
            stats["sections"] += 1
        if stats["parts"] % 200 == 0:
            print(f"  {stats['parts']} parts, {stats['sections']} sections", flush=True)
    W.finish("Rhode Island Code of Regulations (RICR), active rules, from the Secretary of State's rules portal. "
             "Each concept links the official PDF (`official_pdf`).", stats)

if __name__ == "__main__":
    main()

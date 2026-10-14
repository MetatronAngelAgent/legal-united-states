#!/usr/bin/env python3
"""
ma_cmr_to_okf.py — Code of Massachusetts Regulations (CMR), mass.gov law library -> OKF v0.2.

The official CMR is a paid print product of the Secretary of the Commonwealth; mass.gov posts
"unofficial version ... for the convenience of the public" copies, one regulation (chapter) per
page, each with downloads. We take the current text = the download whose label says
"(effective <date>)" (latest date; DOCX preferred, then PDF), skipping proposals, notices and
testimony. Sections are cut at "15.01: Heading" lines. Concepts are marked unofficial.

robots.txt allows /law-library/, /regulations/ and /doc/. Polite: 2 s between requests.
Output: <out>/massachusetts/regulations/title-<T>/chapter-<C>/sec-<C>-<NN>.md (citation 101 CMR 15.01)
Usage:  python3 ma_cmr_to_okf.py --cache ma_cache --out legal       (needs pdftotext for PDFs)
"""
import argparse, datetime as dt, html, os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import regs_common as R
import uslm_to_okf as U

BASE = "https://www.mass.gov"
INDEX = BASE + "/info-details/code-of-massachusetts-regulations-cmr-by-number"
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}

def get(url, path):
    p = R.fetch(url, path, delay=2.0)
    return open(p, encoding="utf-8", errors="replace").read() if p else ""

def eff_date(label):
    m = re.search(r"effective\s+(?:date:?\s*)?([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", label, re.I)
    if m and m.group(1).lower() in MONTHS:
        return dt.date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
    m = re.search(r"effective\s+(\d{1,2})/(\d{1,2})/(\d{2,4})", label, re.I)
    if m:
        y = int(m.group(3)); y += 2000 if y < 100 else 0
        try:
            return dt.date(y, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None

def doc_text(path):
    head = open(path, "rb").read(8)
    if head.startswith(b"%PDF"):
        return subprocess.run(["pdftotext", "-enc", "UTF-8", path, "-"], capture_output=True, text=True, timeout=300).stdout
    if path.endswith(".txt"):
        return open(path, encoding="utf-8", errors="replace").read()
    if head.startswith(b"PK"):
        try:
            import docx
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "python-docx"]); import docx
        d = docx.Document(path)
        out = []
        for el in d.element.body.iterchildren():
            tag = el.tag.rsplit("}", 1)[-1]
            if tag == "p":
                out.append("".join(t.text or "" for t in el.iter() if t.tag.endswith("}t")))
            elif tag == "tbl":
                for tr in el.iter():
                    if tr.tag.endswith("}tr"):
                        cells = ["".join(t.text or "" for t in tc.iter() if t.tag.endswith("}t")) for tc in tr if tc.tag.endswith("}tc")]
                        out.append("| " + " | ".join(U.normspace(c) for c in cells) + " |")
            out.append("")
        return "\n".join(out)
    return ""

def paras(lines):
    out, cur = [], ""
    for s in lines:
        s = s.strip()
        if not s:
            if cur: out.append(cur); cur = ""
            continue
        if re.match(r"^(\(\w{1,4}\)|\d+\.|[a-z]\.)\s", s) and cur:
            out.append(cur); cur = s
        elif s.startswith("|"):
            if cur: out.append(cur); cur = ""
            out.append(s)
        else:
            cur = (cur[:-1] + s) if cur.endswith("-") and s[:1].islower() else (cur + " " + s if cur else s)
    if cur: out.append(cur)
    res, tbl = [], []
    for p in out:                                    # group consecutive table rows
        if p.startswith("|"):
            tbl.append(p); continue
        if tbl:
            w = max(r.count("|") for r in tbl) - 1
            res.append("\n".join([tbl[0], "|" + "---|" * max(w, 1)] + tbl[1:])); tbl = []
        res.append(U.normspace(p))
    if tbl:
        w = max(r.count("|") for r in tbl) - 1
        res.append("\n".join([tbl[0], "|" + "---|" * max(w, 1)] + tbl[1:]))
    return res

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", default="legal")
    ap.add_argument("--titles", default="")
    a = ap.parse_args()
    want = {x.strip().lower() for x in a.titles.split(",") if x.strip()}
    idx = get(INDEX, os.path.join(a.cache, "index.html"))
    titles = []
    for m in re.finditer(r'href="(?:https://www\.mass\.gov)?(/law-library/(\d+[a-z]?)-cmr)"', idx):
        if m.group(2) not in [t[0] for t in titles]:
            titles.append((m.group(2), m.group(1)))
    W = R.Writer(a.out, "massachusetts", "Massachusetts", "ma_cmr_to_okf/1.0", "mass-gov",
                 "Code of Massachusetts Regulations — mass.gov law library (unofficial online version)", INDEX,
                 "Public domain (state government publication); mass.gov posts these as unofficial copies",
                 "Code of Massachusetts Regulations, unofficial versions posted on mass.gov", official=False)
    st = {"titles": 0, "regulations": 0, "no_current_download": 0, "sections": 0, "chapter_level": 0}
    for t, href in titles:
        if want and t not in want:
            continue
        tp = get(BASE + href, os.path.join(a.cache, f"title-{t}.html"))
        st["titles"] += 1
        tname = re.search(r"<title>\s*([^<|]+)", tp)
        W.name_dir(os.path.join(W.root, f"title-{t}"), U.normspace(html.unescape(tname.group(1))) if tname else f"{t} CMR")
        regs = []
        for m in re.finditer(r'href="(/regulations/([^"]+))"', tp):
            if m.group(1) not in regs and re.match(r"^\d+[A-Za-z]?-CMR-", m.group(2)):
                regs.append(m.group(1))
        for rhref in regs:
            rp = get(BASE + rhref, os.path.join(a.cache, "regs", rhref.rsplit("/", 1)[1][:150] + ".html"))
            if not rp:
                continue
            st["regulations"] += 1
            name = re.search(r"<title>\s*([^<|]+)", rp)
            name = U.normspace(html.unescape(name.group(1))) if name else rhref
            mnum = re.match(r"^(\d+[A-Z]?)\s+CMR\s+(\d+[A-Z]?)\.00:?\s*(.*)$", name)
            chap = mnum.group(2) if mnum else re.sub(r"^.*CMR-(\d+[A-Z]?)\d\d.*$", r"\1", rhref)
            cname = mnum.group(3) if mnum else name
            main = rp[rp.find("<main"):] if "<main" in rp else rp
            cands = []
            for m in re.finditer(r'href="(https://www\.mass\.gov/doc/[^"]+/download)"[^>]*>(.*?)</a>', main, re.S):
                label = U.normspace(html.unescape(re.sub(r"<[^>]+>", " ", m.group(2))))
                if re.search(r"proposed|notice of|^notice|testimony|public hearing|hearing (?:notice|officer report)|summary of|redline|"
                             r"small business|comments?\b|amendments? (?:filed|adopted)|emergency (?:filing|amendment)|fact sheet|faq|guidance", label, re.I):
                    continue
                d = eff_date(label)
                if not d:
                    d = dt.date(1900, 1, 1) if re.match(r"^\d+[A-Z]?\s+CMR\s+\d+", label) else dt.date(1800, 1, 1)
                kind = "docx" if "DOCX" in label else "pdf" if "PDF" in label else "txt" if "TXT" in label else "doc"
                cands.append((d, {"docx": 3, "pdf": 2, "txt": 1, "doc": 0}[kind], m.group(1), label, kind))
            if not cands:
                st["no_current_download"] += 1; continue
            cands.sort(reverse=True)
            if cands[0][0].year == 1800 and len({re.sub(r",\s*Open.*$", "", c[3]) for c in cands}) > 1:
                st["no_current_download"] += 1; continue            # several undated documents: can't tell which is the rule
            best = [c for c in cands if c[0] == cands[0][0]]
            best = [c for c in best if c[4] != "doc"] or best
            d, _, url, label, kind = best[0]
            if kind == "doc":
                st["no_current_download"] += 1; continue      # legacy .doc only; needs antiword/LibreOffice
            fp = R.fetch(url, os.path.join(a.cache, "docs", f"{t}-{chap}.{kind}"), delay=2.0)
            if not fp:
                st["no_current_download"] += 1; continue
            text = doc_text(fp)
            lines = [l for l in text.splitlines() if not re.match(r"^\s*(\d+ CMR: [A-Z ,&]+|\d{1,4}|\(\d+/\d+/\d+\)|.*Official Version.*)\s*$", l)]
            sec_re = re.compile(r"^\s*(" + re.escape(chap) + r"\.\d+[A-Za-z]?)\s*:\s*(.+)$")
            starts = [i for i, l in enumerate(lines) if sec_re.match(l)]
            # drop the table-of-contents block: keep the LAST occurrence of each section number
            last = {}
            for i in starts:
                last[sec_re.match(lines[i]).group(1)] = i
            starts = sorted(last.values())
            dirs = [f"title-{t}", f"chapter-{U.slug(chap)}"]
            W.name_dir(os.path.join(W.root, *dirs), f"{t} CMR {chap}.00: {cname}")
            hier = f"{t} CMR / {t} CMR {chap}.00: {cname}"
            extra = {"effective_date": d.isoformat() if d.year > 1900 else "", "source_document": url}
            if d.year == 1800:
                extra["selection_note"] = "only regulation document on the page (label had no effective date)"
            if not starts:
                W.write(dirs, f"sec-{U.slug(chap)}-00", f"{t} CMR {chap}.00", cname, "\n\n".join(paras(lines)),
                        BASE + rhref, hier, extra=extra)
                st["chapter_level"] += 1; continue
            for k, i in enumerate(starts):
                m = sec_re.match(lines[i])
                end = starts[k + 1] if k + 1 < len(starts) else len(lines)
                body = lines[i + 1:end]
                hist = []
                if k + 1 == len(starts):
                    for j, l in enumerate(body):
                        if re.match(r"^\s*REGULATORY AUTHORITY", l):
                            hist = body[j:]; body = body[:j]; break
                W.write(dirs, f"sec-{U.slug(m.group(1))}", f"{t} CMR {m.group(1)}", m.group(2), "\n\n".join(paras(body)),
                        BASE + rhref, hier, history="\n\n".join(paras(hist)), extra=extra)
                st["sections"] += 1
        print(f"{t} CMR: {st['regulations']} regs, {st['sections']} sections, {st['no_current_download']} without current text", flush=True)
    W.finish("Code of Massachusetts Regulations from mass.gov's law library. **Unofficial versions** (the official CMR "
             "is sold in print by the Secretary of the Commonwealth); each concept records the effective date of the text used.", st)

if __name__ == "__main__":
    main()

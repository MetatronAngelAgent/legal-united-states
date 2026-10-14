#!/usr/bin/env python3
"""
mo_csr_to_okf.py — Missouri Code of State Regulations (official, Secretary of State) -> OKF v0.2.

Source: https://www.sos.mo.gov/adrules/csr/csr — current edition, one PDF per chapter
(.../cmsimages/adrules/csr/current/<T>csr/<T>c<D>-<C>.pdf). Text via pdftotext (reading order
handles the two-column layout); page headers/footers removed; rules cut at their
"<T> CSR <D>-<C>.<NNN> Heading" lines; AUTHORITY / history notes kept separately.

Output: <out>/missouri/regulations/title-T/division-D/chapter-C/sec-<D>-<C>.<NNN>.md
Usage:  python3 mo_csr_to_okf.py --cache mo_cache --out legal      (needs pdftotext / poppler)
"""
import argparse, html, os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import regs_common as R
import uslm_to_okf as U

BASE = "https://www.sos.mo.gov"
RULE = re.compile(r"^(\d+) CSR (\d+)-(\d+)\.(\d+)\s+(.*)$")
NOISE = [re.compile(p) for p in (
    r"^Secretary of State$", r"^\(\d{1,2}/\d{1,2}/\d{2,4}\)$", r"^CODE OF STATE REGULATIONS", r"^\d{1,4}$",
    r"^\d+\s+CODE OF STATE REGULATIONS", r"^\d+ CSR \d+-\d+—", r"^(TITLE|DIVISION) \d+—", r"^Division \d+—",
    r"^Chapter \d+—", r"^rules of$", r"^Title$", r"^Page$")]
LABEL = re.compile(r"^(\((\d+)\)|\(([A-Z])\)|(\d+)\.|([a-z])\.|\(([IVX]+|[ivx]+)\))\s")

def two_column_text(pdf):
    """pdftotext -layout, then cut each page at its column gutters (2 or 3 columns) and read the
    columns left to right. Plain reading-order extraction mis-orders some multi-column pages."""
    raw = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", pdf, "-"], capture_output=True, text=True, timeout=300).stdout
    out = []
    for page in raw.split("\f"):
        lines = page.splitlines()
        body = [l for l in lines if l.strip()]
        w = max((len(l) for l in body), default=0)
        good = []
        for c in range(int(w * 0.25), int(w * 0.8)):
            long_ = [l for l in body if len(l.rstrip()) > c + 1]
            if len(long_) < max(8, 0.5 * len(body)):
                continue
            if sum(1 for l in long_ if l[c - 1:c + 1].strip() == "") / len(long_) >= 0.95:
                good.append(c)
        cuts = []
        for c in good:
            if not cuts or c - cuts[-1][1] > 1:
                cuts.append([c, c])
            else:
                cuts[-1][1] = c
        cuts = [a for a, b in cuts if b - a >= 0]
        if cuts:
            def split(l):
                parts, start = [], 0
                for c in cuts:
                    cc = c
                    if l[cc - 1:cc + 1].strip():            # text crosses the gutter on this line: move to next gap
                        m = re.search(r"\s{2,}", l[cc:cc + 15])
                        cc = cc + m.start() if m else cc
                    parts.append(l[start:cc]); start = cc
                parts.append(l[start:])
                return [x.strip() for x in parts]
            cols = [split(l) for l in lines]
            for k in range(len(cuts) + 1):
                out += [c[k] for c in cols] + [""]
        else:
            out += [l.strip() for l in lines] + [""]
    return "\n".join(out)

def clean_lines(txt):
    lines = [l.rstrip().replace("\f", "") for l in txt.splitlines()]
    out = []
    for i, l in enumerate(lines):
        s = l.strip()
        if not s:
            out.append(""); continue
        if any(p.search(s) for p in NOISE):
            continue
        nxt = next((x.strip() for x in lines[i + 1:i + 3] if x.strip()), "")
        if nxt == "Secretary of State":                     # the Secretary's name above it
            continue
        if re.fullmatch(r"[A-Z0-9 ,.&'’—\-()]{3,80}", s) and not re.search(r"[a-z]", s) and not s.startswith("("):
            continue                                        # running all-caps headers
        out.append(s)
    return out

def paragraphs(lines):
    paras, cur = [], ""
    for s in lines:
        if not s:
            continue
        starts = LABEL.match(s) or re.match(r"^(PURPOSE|AUTHORITY|EDITOR[’']S NOTE|PUBLISHER[’']S NOTE|\*Original authority)", s)
        if starts and cur:
            paras.append(cur); cur = s
        elif cur:
            if cur.endswith("-") and s[:1].islower():
                cur = cur[:-1] + s
            else:
                cur += " " + s
        else:
            cur = s
    if cur:
        paras.append(cur)
    out = []
    for p in paras:
        m = LABEL.match(p)
        lvl = 0
        if m:
            lvl = 0 if m.group(2) else 1 if m.group(3) else 2 if m.group(4) else 3 if m.group(5) else 4
        out.append("  " * lvl + U.normspace(p))
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", default="legal")
    ap.add_argument("--titles", default="")
    a = ap.parse_args()
    idx = R.fetch(BASE + "/adrules/csr/csr", os.path.join(a.cache, "csr.html"), delay=0)
    page = open(idx, encoding="utf-8", errors="replace").read()
    titles = []
    for m in re.finditer(r'href="(?:https://www\.sos\.mo\.gov)?(/adrules/csr/current/(\d+)csr(?:/\d+csr)?)"[^>]*>\s*Title\s+\d+\s*-\s*([^<]+)', page):
        if m.group(2) not in [t[0] for t in titles]:
            titles.append((m.group(2), m.group(1), html.unescape(m.group(3)).strip()))
    want = {x.strip() for x in a.titles.split(",") if x.strip()}
    W = R.Writer(a.out, "missouri", "Missouri", "mo_csr_to_okf/1.0", "mo-sos",
                 "Missouri Code of State Regulations — Secretary of State (official PDF)", BASE + "/adrules/csr/csr",
                 "Public domain (state government publication)", "Missouri Code of State Regulations, current edition")
    stats = {"titles": 0, "chapters": 0, "chapters_failed": 0, "rules": 0}
    for t, href, tname in titles:
        if want and t not in want:
            continue
        tp = R.fetch(BASE + href, os.path.join(a.cache, f"title-{t}.html"), delay=2.0)
        if not tp:
            continue
        stats["titles"] += 1
        tpage = open(tp, encoding="utf-8", errors="replace").read()
        W.name_dir(os.path.join(W.root, f"title-{t}"), f"Title {t} — {tname}")
        pdfs = []
        for m in re.finditer(r'href="([^"]*/adrules/csr/current/\d+csr/(\d+)c(\d+)-(\d+[a-z]?)\.pdf)"(?:[^>]*aria-label="([^"]*)")?', tpage, re.I):
            if m.group(1) not in [p[0] for p in pdfs]:
                pdfs.append((m.group(1), m.group(3), m.group(4), html.unescape(m.group(5) or "")))
        for url, div, ch, aria in pdfs:
            url = url if url.startswith("http") else ("https:" + url) if url.startswith("//") else BASE + url
            fn = R.fetch(url, os.path.join(a.cache, f"{t}csr", os.path.basename(url)), delay=2.0)
            if not fn:
                stats["chapters_failed"] += 1; continue
            stats["chapters"] += 1
            try:
                txt = two_column_text(fn)
            except Exception:
                stats["chapters_failed"] += 1; continue
            lines = clean_lines(txt)
            last = {}
            for i, s in enumerate(lines):
                m = RULE.match(s)
                if m and m.group(1) == t and not re.search(r"\.\s?\.\s?\.|\.{3,}|\(Moved to|\(Rescinded", s) \
                        and not (i + 1 < len(lines) and re.search(r"\.\s?\.\s?\.|\.{3,}", lines[i + 1])):
                    last[f"{m.group(2)}-{m.group(3)}.{m.group(4)}"] = i     # last occurrence = the rule itself, not the TOC
            starts = sorted(last.values())
            dname = re.sub(r"\s*\|\s*Chapter.*$", "", aria).strip()
            W.name_dir(os.path.join(W.root, f"title-{t}", f"division-{div}"), (dname.title() if dname else f"Division {div}"))
            cname = re.sub(r"^.*\|\s*", "", aria).strip() if "|" in aria else f"Chapter {ch}"
            W.name_dir(os.path.join(W.root, f"title-{t}", f"division-{div}", f"chapter-{ch}"), cname)
            seen = set()
            for k, i in enumerate(starts):
                m = RULE.match(lines[i])
                num = f"{m.group(2)}-{m.group(3)}.{m.group(4)}"
                head = m.group(5)
                j, added = i + 1, 0
                while j < len(lines) and added < 2 and not head.endswith("."):
                    if not lines[j]:
                        j += 1; continue
                    if re.match(r"^(PURPOSE|EDITOR|AUTHORITY|\*Original|\(|\d+\.)", lines[j]) or RULE.match(lines[j]) \
                            or (len(head) < 30 and not head.endswith("-")):
                        break
                    head = head[:-1] + lines[j] if head.endswith("-") else head + " " + lines[j]
                    j += 1; added += 1
                end = starts[k + 1] if k + 1 < len(starts) else len(lines)
                paras = paragraphs(lines[j:end])
                text, hist, mode = [], [], "text"
                for p in paras:
                    if re.match(r"^\s*(AUTHORITY|\*Original authority)", p):
                        mode = "hist"
                    (hist if mode == "hist" else text).append(p)
                cite = f"{t} CSR {num}"
                if cite in seen:
                    continue
                seen.add(cite)
                W.write([f"title-{t}", f"division-{div}", f"chapter-{ch}"], f"sec-{U.slug(num)}", cite, head,
                        "\n\n".join(text), url, f"Title {t} {tname} / {dname or 'Division ' + div} / {cname}",
                        history="\n\n".join(hist))
                stats["rules"] += 1
        print(f"title {t}: {stats['rules']} rules so far ({stats['chapters']} chapters, {stats['chapters_failed']} failed)", flush=True)
    W.finish("Official Missouri Code of State Regulations (Secretary of State, Administrative Rules Division), current edition.", stats)

if __name__ == "__main__":
    main()

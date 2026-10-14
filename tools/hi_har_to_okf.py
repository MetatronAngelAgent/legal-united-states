#!/usr/bin/env python3
"""
hi_har_to_okf.py — Hawaii Administrative Rules -> OKF v0.2.

Hawaii has no compiled code: each department posts its own chapter PDFs. The Lieutenant
Governor's page (https://ltgov.hawaii.gov/the-office/administrative-rules/) links every
department's rules page; we visit those pages (and same-site sub-pages whose URL mentions
rules/HAR/chapter/title, one level deep), collect chapter PDFs whose name or link text looks
like a HAR chapter ("Title 11 Chapter 60.1", "HAR 11-60.1", "chapter-11-60-1"), extract text
(pdftotext; optional OCR via tesseract for scanned files with --ocr), and cut sections at
"§11-60.1-1 Heading" lines. robots.txt is honoured per host; 2 s between requests.

Output: <out>/hawaii/regulations/title-T/chapter-<T-C>/sec-<T-C-N>.md (citation HAR § 11-60.1-1)
Usage:  python3 hi_har_to_okf.py --cache hi_cache --out legal [--ocr]
"""
import argparse, html, os, re, subprocess, sys, tempfile, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import regs_common as R
import uslm_to_okf as U

INDEX = "https://ltgov.hawaii.gov/the-office/administrative-rules/"
CH = re.compile(r"(?:\bHAR\b|title|chapter|ch\.?)[\s_\-]*(\d{1,2})[\s_\-]+(?:chapter|ch\.?)?[\s_\-]*(\d{1,3}(?:\.\d+)?[A-Z]?)", re.I)
DEPT_TITLE = {"Accounting and General Services": "3", "Agriculture": "4", "Attorney General": "5", "Budget": "6",
              "Defense": "7", "Education": "8", "Hawaiian Home Lands": "10", "Health": "11", "Labor": "12",
              "Land and Natural Resources": "13", "Human Resources Development": "14", "Business, Economic": "15",
              "Commerce and Consumer Affairs": "16", "Human Services": "17", "Taxation": "18", "Transportation": "19",
              "Corrections": "23"}
def dept_title(dname):
    return next((v for k, v in DEPT_TITLE.items() if k in dname), None)

SEC = re.compile(r"^\s*§\s*(\d{1,2}-\d{1,3}(?:\.\d+)?[A-Z]?-\d+(?:\.\d+)?[A-Za-z]?)\s+(?!(?:is|are|to|and|through)\b)(.{2,200})$")

def text_of(pdf, ocr):
    cached = pdf + ".ocr.txt"
    if os.path.exists(cached):
        return open(cached, encoding="utf-8").read(), "ocr"
    t = subprocess.run(["pdftotext", "-enc", "UTF-8", pdf, "-"], capture_output=True, text=True, timeout=600).stdout
    info = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True, timeout=60).stdout
    pages = int((re.search(r"Pages:\s+(\d+)", info) or [0, 1])[1])
    if len(re.sub(r"\s", "", t)) >= 300 * pages:          # a real text layer on (nearly) every page
        return t, "text"
    if not ocr:
        return "", "scanned"
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["pdftoppm", "-r", "200", "-gray", pdf, os.path.join(d, "p")], timeout=3600)
        out = []
        for f in sorted(os.listdir(d)):
            out.append(subprocess.run(["tesseract", os.path.join(d, f), "-", "--psm", "4"], capture_output=True, text=True, timeout=600).stdout)
    t = "\n".join(out)
    open(cached, "w", encoding="utf-8").write(t)
    return t, "ocr"

def links(page, base):
    for m in re.finditer(r'href="([^"#]+)"[^>]*>(.*?)</a>', page, re.S):
        yield urllib.parse.urljoin(base, html.unescape(m.group(1))), U.normspace(html.unescape(re.sub(r"<[^>]+>", " ", m.group(2))))

def paras(lines):
    out, cur = [], ""
    for s in lines:
        s = s.strip()
        if not s or re.match(r"^\d{1,3}-\d{1,4}$|^\d{1,4}$", s):          # blank / page numbers like 11-60.1-12
            if cur and not s: out.append(cur); cur = ""
            continue
        if re.match(r"^\(\w{1,4}\)\s", s) and cur:
            out.append(cur); cur = s
        else:
            cur = (cur[:-1] + s) if cur.endswith("-") and s[:1].islower() else (cur + " " + s if cur else s)
    if cur: out.append(cur)
    return [U.normspace(p) for p in out]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", default="legal")
    ap.add_argument("--ocr", action="store_true")
    a = ap.parse_args()
    idx = open(R.fetch(INDEX, os.path.join(a.cache, "index.html"), delay=0), encoding="utf-8", errors="replace").read()
    depts = [(u, t) for u, t in links(idx, INDEX) if t.startswith("Department") and "hawaii.gov" in u]
    pdfs = {}
    for durl, dname in depts:
        host = urllib.parse.urlsplit(durl).netloc
        seen_pages, queue = set(), [durl]
        for depth in range(2):
            nxt = []
            for pu in queue:
                if pu in seen_pages: continue
                seen_pages.add(pu)
                fp = R.fetch(pu, os.path.join(a.cache, "pages", re.sub(r"[^\w.-]", "_", pu)[-150:] + ".html"), delay=2.0)
                if not fp: continue
                pg = open(fp, encoding="utf-8", errors="replace").read()
                for u, t in links(pg, pu):
                    uh = urllib.parse.urlsplit(u)
                    if u.lower().split("?")[0].endswith(".pdf"):
                        lab = t + " " + urllib.parse.unquote(os.path.basename(uh.path))
                        if re.search(r"propos|draft|notice|hearing|testimony|ramseyer|summary|form\b|rationale|redline|comment", lab, re.I):
                            continue
                        m = CH.search(lab)
                        t_c = (m.group(1), m.group(2)) if m else None
                        if not t_c and re.search(r"/har", uh.path, re.I):
                            m2 = re.search(r"\b(\d{1,2})-(\d{1,3}(?:\.\d+)?[A-Z]?)\b", lab)
                            t_c = (m2.group(1), m2.group(2)) if m2 else None
                        if not t_c and dept_title(dname):
                            m3 = re.search(r"(?:\bHAR\b|chapter|ch\.|har_)[\s_\-]*(\d{1,3}(?:\.\d+)?[A-Z]?)\b", lab, re.I)
                            t_c = (dept_title(dname), m3.group(1)) if m3 else None
                        dt_ = dept_title(dname)
                        if t_c and dt_ and t_c[0].lstrip("0") != dt_:
                            t_c = None                        # e.g. a statute (HRS) chapter or another title's rule
                        if t_c and u not in pdfs and re.fullmatch(r"\d{1,2}", t_c[0]) and 1 <= int(t_c[0]) <= 23:
                            ch_ = re.sub(r"^0+(?=\d)", "", t_c[1])
                            pdfs[u] = (dname, str(int(t_c[0])), ch_, lab)
                    elif depth == 0 and uh.netloc == host and re.search(r"rule|har|chapter|title", uh.path, re.I) and u not in seen_pages:
                        nxt.append(u.split("#")[0])
            queue = nxt[:80]
        print(f"{dname}: {sum(1 for v in pdfs.values() if v[0] == dname)} chapter PDFs", flush=True)
    W = R.Writer(a.out, "hawaii", "Hawaii", "hi_har_to_okf/1.0", "hi-departments",
                 "Hawaii Administrative Rules — department rule pages (linked from the Lieutenant Governor)", INDEX,
                 "Public domain (state government publication)", "Hawaii Administrative Rules as posted by each department")
    st = {"departments": len(depts), "chapter_pdfs": len(pdfs), "downloaded": 0, "scanned_no_text": 0, "ocr": 0,
          "sections": 0, "chapter_level": 0}
    done_ch = {}
    for u, (dname, t, c, lab) in sorted(pdfs.items(), key=lambda kv: (int(kv[1][1]), kv[1][2])):
        key = f"{t}-{c}"
        fp = R.fetch(u, os.path.join(a.cache, "pdf", re.sub(r"[^\w.-]", "_", urllib.parse.urlsplit(u).path)[-150:]), delay=2.0)
        if not fp: continue
        st["downloaded"] += 1
        try:
            txt, how = text_of(fp, a.ocr)
        except Exception:
            continue
        if len(re.sub(r"\s", "", txt)) < 200:
            st["scanned_no_text"] += 1; continue
        if how == "ocr": st["ocr"] += 1
        lines = txt.splitlines()
        starts, last = [], {}
        for i, l in enumerate(lines):
            m = SEC.match(l)
            if m and m.group(1).startswith(key + "-"):
                last[m.group(1)] = i                          # last occurrence: skips the chapter's own table of contents
        starts = sorted(last.values())
        if key in done_ch and len(starts) <= done_ch[key]:
            continue                                          # duplicate posting of the same chapter
        done_ch[key] = len(starts)
        dirs = [f"title-{t}", f"chapter-{U.slug(key)}"]
        W.name_dir(os.path.join(W.root, f"title-{t}"), f"Title {t} — {dname}")
        W.name_dir(os.path.join(W.root, *dirs), f"Chapter {key} ({lab[:120]})")
        extra = {"text_extraction": "OCR (scanned PDF)" if how == "ocr" else "", "source_document": u}
        hier = f"Title {t} ({dname}) / Chapter {key}"
        if not starts:
            W.write(dirs, f"chapter-{U.slug(key)}", f"HAR Chapter {key}", lab[:200], "\n\n".join(paras(lines)), u, hier, extra=extra)
            st["chapter_level"] += 1; continue
        for k, i in enumerate(starts):
            m = SEC.match(lines[i])
            end = starts[k + 1] if k + 1 < len(starts) else len(lines)
            head, first = m.group(2).strip(), ""
            hm = re.match(r"^(.*?[^A-Z§]\.)\s+(?=[A-Z(\[])(.*)$", head)   # "Purpose. These rules ..." -> heading / body
            if hm and len(hm.group(1)) < 160:
                head, first = hm.group(1), hm.group(2)
            body = ([first] if first else []) + lines[i + 1:end]
            text = "\n\n".join(paras(body))
            hist = ""
            hx = re.search(r"\[\s*(Eff|Am|Ren|Comp|Auth)\b", text)
            if hx:
                text, hist = text[:hx.start()].rstrip(), text[hx.start():]
            W.write(dirs, f"sec-{U.slug(m.group(1))}", f"HAR § {m.group(1)}", head, text, u, hier, history=hist, extra=extra)
            st["sections"] += 1
    W.finish("Hawaii Administrative Rules, gathered from each department's official rules page (Hawaii has no single "
             "compiled code). Scanned chapters without a text layer are skipped unless run with --ocr.", st)

if __name__ == "__main__":
    main()

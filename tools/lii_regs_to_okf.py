#!/usr/bin/env python3
"""
lii_regs_to_okf.py — state regulations from Cornell LII's state-regulations collection -> OKF v0.2.

For states whose official administrative code is only on a vendor platform (California CCR and
New York NYCRR on Thomson Reuters' government Westlaw site), LII republishes the text, updated
quarterly: https://www.law.cornell.edu/regulations/<state>.

LICENSE: LII's state-regulation pages are CC BY-NC-SA — fine for a personal / research knowledge
base, NOT for commercial redistribution (ask permissions@liicornell.org). Every concept says so.
POLITENESS: LII's robots.txt asks for Crawl-delay: 10. This crawler waits 10 s between requests,
caches every page (re-runs and interruptions never re-fetch), and identifies itself. At that rate a
large code takes days to weeks; run it in the background on your own machine and let it resume.

Crawl: state index -> title -> division/chapter/subchapter/article/part pages -> section pages
(any /regulations/<state>/ link whose last path segment is not a hierarchy level).
Output: <out>/<state>/regulations/<hierarchy dirs>/sec-<n>.md

Usage:  python3 lii_regs_to_okf.py --state california --cache lii_cache --out legal [--titles 1,2] [--max-pages N]
"""
import argparse, html, json, os, re, subprocess, sys, time, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import regs_common as R
import uslm_to_okf as U
try:
    from bs4 import BeautifulSoup
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "beautifulsoup4", "lxml"])
    from bs4 import BeautifulSoup

BASE = "https://www.law.cornell.edu"
LEVEL = re.compile(r"^(title|division|subdivision|chapter|subchapter|article|subarticle|part|subpart|group|subgroup|appendix)-[\w.\-]+$", re.I)
LABEL = {"california": ("California", "Cal. Code Regs."), "new-york": ("New York", "N.Y. Comp. Codes R. & Regs.")}
LICENSE = ("CC BY-NC-SA (Cornell LII republication; non-commercial use only). Underlying regulations are "
           "government edicts; the official text is the state's publication.")

class Crawler:
    def __init__(self, cache, state, max_pages):
        self.cache, self.state, self.max_pages, self.fetched = cache, state, max_pages, 0

    def page(self, path):
        fn = os.path.join(self.cache, self.state, re.sub(r"[^\w.\-]", "_", path.strip("/"))[-180:] + ".html")
        if not os.path.exists(fn):
            if self.max_pages and self.fetched >= self.max_pages:
                return None
            self.fetched += 1
        p = R.fetch(BASE + path, fn, delay=10.0)       # robots.txt: Crawl-delay 10
        return open(p, encoding="utf-8", errors="replace").read() if p else None

    def links(self, s):
        out = []
        for m in re.finditer(r'href="(/regulations/' + re.escape(self.state) + r'/[^"#?]+)"', s):
            if m.group(1) not in out:
                out.append(m.group(1))
        return out

def section_md(div):
    """LII statereg-text -> markdown paragraphs with indentation from indentN classes."""
    out = []
    def walk(el):
        for c in el.children:
            if getattr(c, "name", None) == "div" and any(x.startswith("indent") for x in c.get("class", [])):
                lvl = next((int(x[6:]) for x in c["class"] if x.startswith("indent") and x[6:].isdigit()), 0)
                own = "".join(str(x) if not getattr(x, "name", None) else
                              ("" if x.name == "div" and any(k.startswith("indent") for k in x.get("class", [])) else x.get_text(" "))
                              for x in c.children)
                t = U.normspace(html.unescape(re.sub(r"<[^>]+>", " ", own)))
                if t:
                    out.append("  " * lvl + t)
                walk(c)
            elif getattr(c, "name", None) == "table":
                rows = [[U.normspace(td.get_text(" ")).replace("|", "\\|") for td in tr.find_all(["td", "th"])] for tr in c.find_all("tr")]
                rows = [r for r in rows if any(r)]
                if rows:
                    w = max(len(r) for r in rows); rows = [r + [""] * (w - len(r)) for r in rows]
                    out.append("\n".join(["| " + " | ".join(rows[0]) + " |", "|" + "---|" * w] + ["| " + " | ".join(r) + " |" for r in rows[1:]]))
            elif getattr(c, "name", None) in ("p", "div", "blockquote", "pre"):
                if c.find("div", class_=re.compile("indent")):
                    walk(c)
                else:
                    t = U.normspace(c.get_text(" "))
                    if t: out.append(t)
            elif getattr(c, "name", None) is None and U.normspace(str(c)):
                out.append(U.normspace(str(c)))
    walk(div)
    return "\n\n".join(out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True, choices=sorted(LABEL))
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", default="legal")
    ap.add_argument("--titles", default="", help="comma list of title numbers to limit the crawl")
    ap.add_argument("--max-pages", type=int, default=0, help="stop after fetching N new pages (cache hits are free)")
    a = ap.parse_args()
    st = a.state
    label, cite_prefix = LABEL[st]
    C = Crawler(a.cache, st, a.max_pages)
    W = R.Writer(a.out, st, label, "lii_regs_to_okf/1.0", "cornell-lii",
                 f"{label} regulations — Cornell LII state regulations", f"{BASE}/regulations/{st}", LICENSE,
                 f"{label} administrative code as republished by Cornell LII (updated quarterly)", official=False)
    want = {x.strip() for x in a.titles.split(",") if x.strip()}
    root = f"/regulations/{st}"
    idx = C.page(root)
    if not idx:
        raise SystemExit("cannot fetch LII index")
    stats = {"pages_fetched_new": 0, "hierarchy_pages": 0, "sections": 0, "sections_pending": 0}
    queue = [p for p in C.links(idx) if re.match(rf"^{root}/title-[\w.\-]+$", p)]
    if want:
        queue = [p for p in queue if p.rsplit("title-", 1)[1] in want]
    seen, sections = set(), []
    while queue:
        p = queue.pop(0)
        if p in seen: continue
        seen.add(p)
        s = C.page(p)
        if s is None:
            stats["sections_pending"] += 1; continue
        stats["hierarchy_pages"] += 1
        for l in C.links(s):
            if l in seen or not l.startswith(p + "/") and not re.match(rf"^{root}/[^/]+$", l):
                continue
            last = l.rstrip("/").rsplit("/", 1)[1]
            if l.startswith(p + "/") and LEVEL.match(last):
                queue.append(l)
            elif not LEVEL.match(last) and l.count("/") == root.count("/") + 1:
                if l not in sections:
                    sections.append(l)
    print(f"{st}: {len(sections)} sections discovered from {stats['hierarchy_pages']} hierarchy pages", flush=True)
    for sp in sections:
        s = C.page(sp)
        if s is None:
            stats["sections_pending"] += 1; continue
        soup = BeautifulSoup(s, "lxml")
        title = U.normspace(soup.title.get_text().split("|")[0]) if soup.title else sp
        crumbs = [U.normspace(a_.get_text()) for a_ in soup.select("#breadcrumb a, .breadcrumb a")
                  if a_.get("href", "").startswith(f"{root}/") and LEVEL.match(a_["href"].rstrip("/").rsplit("/", 1)[1])]
        dirs = []
        for a_ in soup.select("#breadcrumb a, .breadcrumb a"):
            h = a_.get("href", "")
            if h.startswith(f"{root}/"):
                seg = h.rstrip("/").rsplit("/", 1)[1]
                if LEVEL.match(seg):
                    dirs.append(U.slug(seg.lower()))
                    W.name_dir(os.path.join(W.root, *dirs), U.normspace(a_.get_text()))
        body_div = soup.select_one("#tab_default_1 .statereg-text") or soup.select_one("#tab_default_1")
        body = section_md(body_div) if body_div else ""
        notes = soup.select("#tab_default_1 .statereg-note")
        hist = "\n\n".join(U.normspace(n.get_text(" ")) for n in notes[1:]) if len(notes) > 1 else ""
        m = re.match(r"^(.*?§\s*[\w.\-]+)\s*-\s*(.*)$", title)
        cite, head = (m.group(1), m.group(2)) if m else (title, title)
        num = sp.rsplit("/", 1)[1]
        W.write(dirs or ["misc"], f"sec-{U.slug(num.lower())}", cite, head, body, BASE + sp, " / ".join(crumbs),
                history=hist, tags=["lii-republication", "non-commercial"])
        stats["sections"] += 1
        if stats["sections"] % 500 == 0:
            print(f"  {stats['sections']} sections written", flush=True)
    stats["pages_fetched_new"] = C.fetched
    W.finish(f"{label} regulations from Cornell LII's republication (updated quarterly). **Non-commercial license "
             f"(CC BY-NC-SA)** — the official code is published by the state via Westlaw. "
             + ("Crawl incomplete: re-run to continue." if stats["sections_pending"] else ""), stats)

if __name__ == "__main__":
    main()

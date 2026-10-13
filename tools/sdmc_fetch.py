#!/usr/bin/env python3
"""
sdmc_fetch.py — Polite fetcher for the San Diego Municipal Code, self-published by the
Office of the City Clerk on sandiego.gov (chapter index pages) and docs.sandiego.gov
(one PDF per Article/Division).

- Respects robots.txt (checked: /city-clerk/ and docs.sandiego.gov are not disallowed).
- Identifying User-Agent, fixed delay between requests (default 2s), one connection.
- Cache-first: anything already on disk is never re-requested, so re-runs are cheap
  and an interrupted run resumes where it stopped.
- Writes <cache>/manifest.json: chapter -> [ {url, file, bytes} ].

Usage:
  python3 sdmc_fetch.py --cache sdmc_cache            # list + download everything
  python3 sdmc_fetch.py --cache sdmc_cache --list     # list only, no PDF downloads
"""
import argparse, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request, urllib.robotparser

UA = "OKF-LawCorpus/1.0 (open legal-knowledge research; contact " + os.environ.get("LEGAL_US_CONTACT", "see github.com/MetatronAngelAgent/legal-united-states") + ")"
BASE = "https://www.sandiego.gov/city-clerk/officialdocs/municipal-code"
CHAPTERS = list(range(1, 16))

_last = [0.0]

def get(url, delay):
    wait = delay - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    url = urllib.parse.quote(url, safe=":/?=&%#")      # some filenames contain spaces
    for attempt in range(4):                              # retry transient resets with backoff
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
                status = r.status
            _last[0] = time.time()
            return status, data
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            _last[0] = time.time()
            if isinstance(e, urllib.error.HTTPError) or attempt == 3:
                raise
            time.sleep(5 * (attempt + 1) ** 2)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="sdmc_cache")
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.cache, exist_ok=True)

    rp = urllib.robotparser.RobotFileParser("https://www.sandiego.gov/robots.txt")
    rp.read()
    if not rp.can_fetch(UA, BASE + "/chapter-1"):
        sys.exit("robots.txt disallows the chapter pages — stopping.")

    manifest = {}
    for ch in CHAPTERS:
        urls, pg = set(), 0
        while True:                       # index pages are paginated (?page=N, 50 per page)
            suffix = "" if pg == 0 else f"-p{pg}"
            page = os.path.join(a.cache, f"chapter-{ch}{suffix}.html")
            if not os.path.exists(page):
                st, data = get(f"{BASE}/chapter-{ch}" + (f"?page={pg}" if pg else ""), a.delay)
                open(page, "wb").write(data)
            html = open(page, encoding="utf-8", errors="replace").read()
            found = set(re.findall(
                r'href="(https?://docs\.sandiego\.gov/municode/[^"]+\.pdf)"', html, re.I))
            new = found - urls
            urls |= found
            if not new or f'?page={pg + 1}"' not in html or pg > 20:
                break
            pg += 1
        manifest[ch] = [{"url": u} for u in sorted(urls)]
        print(f"chapter {ch}: {len(urls)} documents ({pg + 1} index page(s))", flush=True)

    total = sum(len(v) for v in manifest.values())
    print(f"total documents: {total}", flush=True)
    if a.list:
        json.dump(manifest, open(os.path.join(a.cache, "manifest.json"), "w"), indent=1)
        return

    done = 0
    for ch, docs in manifest.items():
        d = os.path.join(a.cache, f"ch{ch:02d}")
        os.makedirs(d, exist_ok=True)
        for doc in docs:
            fn = os.path.join(d, os.path.basename(doc["url"]))
            doc["file"] = fn
            doc.pop("error", None)
            if not os.path.exists(fn) or os.path.getsize(fn) == 0:
                try:
                    st, data = get(doc["url"], a.delay)
                    if st == 200 and data[:4] == b"%PDF":
                        open(fn, "wb").write(data)
                    else:
                        doc["error"] = f"status {st} / not a PDF"
                except Exception as e:
                    doc["error"] = repr(e)[:200]
            if os.path.exists(fn):
                doc["bytes"] = os.path.getsize(fn)
            done += 1
            if done % 50 == 0:
                print(f"  … {done}/{total}", flush=True)
                json.dump(manifest, open(os.path.join(a.cache, "manifest.json"), "w"), indent=1)
    json.dump(manifest, open(os.path.join(a.cache, "manifest.json"), "w"), indent=1)
    errs = sum(1 for v in manifest.values() for x in v if "error" in x)
    print(f"done: {done} documents, {errs} errors", flush=True)

if __name__ == "__main__":
    main()

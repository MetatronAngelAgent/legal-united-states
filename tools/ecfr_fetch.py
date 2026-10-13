#!/usr/bin/env python3
"""
ecfr_fetch.py — download the full current Code of Federal Regulations (eCFR) from GPO's
govinfo bulk-data repository (govinfo.gov/bulkdata/ECFR), one XML file per title.
NOTE: ecfr.gov's robots.txt disallows its /api/versioner/v1/full/ bulk endpoint, so the
title list comes from the (allowed) titles.json API and the XML from govinfo bulk data,
the channel GPO publishes for bulk use. Cache-first (re-runs only fetch what's missing), identifying
User-Agent, polite delay, retries with backoff.

Usage: python3 ecfr_fetch.py --out ecfr_cache [--titles 21,40]
"""
import argparse, gzip, json, os, sys, time, urllib.request, urllib.error

UA = "OKF-LawCorpus/1.0 (open legal-knowledge research; contact " + os.environ.get("LEGAL_US_CONTACT", "see github.com/MetatronAngelAgent/legal-united-states") + ")"
API = "https://www.ecfr.gov/api/versioner/v1"

def get(url, tries=4):
    for a in range(tries):
        try:
            # the eCFR API requires a compressed response (406 otherwise)
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=900) as r:
                data = r.read()
                return gzip.decompress(data) if r.headers.get("Content-Encoding") == "gzip" else data
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code in (400, 403, 404, 406) or a == tries - 1:
                raise
            time.sleep(10 * (a + 1) ** 2)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="ecfr_cache")
    ap.add_argument("--titles", default="")
    ap.add_argument("--delay", type=float, default=3.0)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    meta = json.loads(get(f"{API}/titles.json"))
    json.dump(meta, open(os.path.join(a.out, "titles.json"), "w"), indent=1)
    want = {int(x) for x in a.titles.split(",") if x.strip()}
    for t in meta["titles"]:
        n = t["number"]
        if t.get("reserved") or (want and n not in want):
            continue
        date = t["up_to_date_as_of"]
        fn = os.path.join(a.out, f"title-{n}.xml")
        if os.path.exists(fn) and os.path.getsize(fn) > 1000:
            continue
        t0 = time.time()
        data = get(f"https://www.govinfo.gov/bulkdata/ECFR/title-{n}/ECFR-title{n}.xml")
        open(fn + ".part", "wb").write(data)
        os.replace(fn + ".part", fn)
        print(f"title {n}: {len(data)/1e6:.1f} MB in {time.time()-t0:.0f}s (as of {date})", flush=True)
        time.sleep(a.delay)
    print("done", flush=True)

if __name__ == "__main__":
    main()

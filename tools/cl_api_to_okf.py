#!/usr/bin/env python3
"""
cl_api_to_okf.py — Judicial branch, daily: new and revised court opinions from the CourtListener
REST API (v4, Free Law Project) -> the same OKF concepts and paths as cl_to_okf.py.

The quarterly bulk files (cl_to_okf.py) carry the full history; this feed closes the gap between
snapshots. Each run asks for opinions created or modified since the last run, then fetches their
clusters and dockets in batches of 100 (about 3 requests per 100 opinions), and writes
  <out>/case-law/<jurisdiction>/<court_id>/<year>/<case-slug>-<opinion_id>.md
Revised opinions are rewritten in place. Privacy-blocked clusters/dockets are skipped.

Needs a free API token (courtlistener.com -> Profile -> Developer Tools -> API token), from
--token or the COURTLISTENER_TOKEN environment variable: anonymous access is limited to 125
requests a day and cannot read clusters. Authenticated limit: 5,000 requests/hour.

Usage
  COURTLISTENER_TOKEN=… python3 cl_api_to_okf.py --out legal --since 2026-10-13
  python3 cl_api_to_okf.py --out legal --since 2026-10-13 --scope federal,california
Stdlib only.
"""
import argparse, datetime as dt, json, os, re, sys, time, urllib.parse, urllib.request, urllib.error
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uslm_to_okf as U
import cl_to_okf as C
from cap_to_okf import Resolver

API = "https://www.courtlistener.com/api/rest/v4"
UA = C.UA

class Api:
    def __init__(self, token, delay):
        self.token, self.delay, self.calls = token, delay, 0
    def get(self, url, tries=5):
        for a in range(tries):
            try:
                h = {"User-Agent": UA, "Accept": "application/json"}
                if self.token: h["Authorization"] = f"Token {self.token}"
                with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=300) as r:
                    self.calls += 1
                    time.sleep(self.delay)
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    raise SystemExit(f"CourtListener refused the request ({e.code}). Set COURTLISTENER_TOKEN "
                                     "(free: courtlistener.com -> Profile -> Developer Tools).")
                if e.code == 429:
                    wait = int(e.headers.get("Retry-After") or 60)
                    print(f"  rate limited; waiting {wait}s", flush=True)
                    time.sleep(min(wait, 3600)); continue
                if e.code == 404 or a == tries - 1:
                    raise
                time.sleep(10 * (a + 1))
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if a == tries - 1: raise
                time.sleep(10 * (a + 1))
    def pages(self, path, params):
        url = f"{API}/{path}/?" + urllib.parse.urlencode(params)
        while url:
            j = self.get(url)
            yield from j.get("results") or []
            url = j.get("next")
    def by_ids(self, path, ids, fields):
        out = {}
        ids = sorted({i for i in ids if i})
        for k in range(0, len(ids), 100):
            chunk = ids[k:k + 100]
            for r in self.pages(path, {"id__in": ",".join(map(str, chunk)), "page_size": 100, "fields": fields}):
                out[r["id"]] = r
        return out

def load_courts(cache):
    """Court table from the (tiny, public) bulk courts file of the newest snapshot."""
    os.makedirs(cache, exist_ok=True)
    keys = urllib.request.urlopen(urllib.request.Request(
        "https://com-courtlistener-storage.s3-us-west-2.amazonaws.com/?list-type=2&prefix=bulk-data/courts-",
        headers={"User-Agent": UA}), timeout=120).read().decode()
    dates = sorted(re.findall(r"bulk-data/courts-(\d{4}-\d{2}-\d{2})\.csv\.bz2", keys))
    date = dates[-1]
    courts = {}
    with C.opener(C.src_of(cache, "courts", date), cache, True) as s:
        for c in C.rows(s):
            courts[c["id"]] = (c["id"], c["full_name"], c["short_name"], c["citation_string"], c["jurisdiction"], C.court_jur(c), c["url"])
    return courts, date

def cid_of(url_or_id):
    if isinstance(url_or_id, int): return url_or_id
    m = re.search(r"/(\d+)/?$", str(url_or_id or ""))
    return int(m.group(1)) if m else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="legal")
    ap.add_argument("--cache", default=os.path.expanduser("~/.cache/cuh-legal/courtlistener"))
    ap.add_argument("--since", required=True, help="YYYY-MM-DD or ISO datetime: opinions created/modified since")
    ap.add_argument("--scope", default="all")
    ap.add_argument("--token", default=os.environ.get("COURTLISTENER_TOKEN", ""))
    ap.add_argument("--delay", type=float, default=0.8)
    ap.add_argument("--min-chars", type=int, default=100)
    ap.add_argument("--split-chars", type=int, default=6000)
    ap.add_argument("--max-opinions", type=int, default=0, help="stop after N (test)")
    ap.add_argument("--report", default="")
    a = ap.parse_args()
    if not a.token:
        raise SystemExit("No CourtListener token: set COURTLISTENER_TOKEN (free at courtlistener.com -> Developer Tools).")
    api = Api(a.token, a.delay)
    scope = {s.strip() for s in a.scope.split(",") if s.strip()}
    run_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    courts, cdate = load_courts(a.cache)
    since = a.since if "T" in a.since else a.since + "T00:00:00Z"
    stats, touched = Counter(), set()
    rep = open(a.report, "a", encoding="utf-8") if a.report else None

    # 1) opinions modified since (includes newly created), in batches
    ofields = ",".join(["id", "cluster", "type", "author_str", "per_curiam", "date_created", "date_modified"] + C.TEXT_FIELDS)
    batch = []
    def flush(batch):
        cl = api.by_ids("clusters", [cid_of(o["cluster"]) for o in batch],
                        "id,docket,case_name,case_name_short,case_name_full,slug,date_filed,date_filed_is_approximate,"
                        "precedential_status,judges,citation_count,blocked,citations")
        dk = api.by_ids("dockets", [cid_of(c.get("docket")) for c in cl.values()], "id,court_id,docket_number,blocked")
        res = flush.res
        for o in batch:
            stats["read"] += 1
            c = cl.get(cid_of(o["cluster"]))
            if not c:
                stats["cluster_missing"] += 1; continue
            d = dk.get(cid_of(c.get("docket"))) or {}
            if c.get("blocked") or d.get("blocked"):
                stats["blocked_skipped"] += 1; continue
            court_id = d.get("court_id") or ""
            court = courts.get(court_id)
            jur = court[5] if court else "other"
            if jur is None:
                stats["foreign_or_test_court"] += 1; continue
            if "all" not in scope and jur not in scope:
                stats["out_of_scope"] += 1; continue
            cites = [f"{x.get('volume')} {x.get('reporter')} {x.get('page')}" for x in
                     sorted(c.get("citations") or [], key=lambda x: C.CITE_ORDER.get(str(x.get("type")), 9))]
            meta = (c.get("case_name") or c.get("case_name_short") or (c.get("case_name_full") or "")[:300],
                    (c.get("case_name_full") or "")[:600], c.get("slug"), c.get("date_filed"),
                    1 if c.get("date_filed_is_approximate") else 0, c.get("precedential_status"), (c.get("judges") or "")[:300],
                    int(c.get("citation_count") or 0), 0, court_id, (d.get("docket_number") or "")[:200], 0, cid_of(c.get("docket")))
            oo = dict(o); oo["per_curiam"] = "t" if o.get("per_curiam") else "f"
            field, paras = C.opinion_paras(oo)
            if len("\n\n".join(paras)) < a.min_chars:
                stats["empty_or_short_skipped"] += 1; continue
            year = (c.get("date_filed") or "")[:4] or "undated"
            ddir = os.path.join(a.out, "case-law", jur, U.slug(court_id or "unknown-court"), year)
            path = os.path.join(ddir, f"{(U.slug(c.get('slug') or 'case')[:60]).strip('-') or 'case'}-{o['id']}.md")
            existed = os.path.exists(path)
            C.write_opinion(path, ddir, oo, o["id"], c["id"], meta, cites, court, jur, year, field, paras, res, run_at,
                            a.split_chars, f"{API}/opinions/{o['id']}/", f"CourtListener API v4 ({run_at[:10]})", stats, touched)
            stats["revised" if existed else "new"] += 1
            if rep:
                rep.write(json.dumps({"branch": "judicial", "feed": "court-opinions", "path": os.path.relpath(path, a.out),
                                      "title": f"{meta[0]} ({court[2] if court else court_id}, {c.get('date_filed')})",
                                      "change": "revised" if existed else "new"}) + "\n")
    flush.res = Resolver(a.out)
    params = {"date_modified__gte": since, "order_by": "date_modified", "page_size": 100, "fields": ofields}
    for o in api.pages("opinions", params):
        batch.append(o)
        if len(batch) >= 100:
            flush(batch); batch = []
            print(f"  {stats['read']:,} opinions, {stats['written']:,} written, {api.calls} API calls", flush=True)
        if a.max_opinions and stats["read"] + len(batch) >= a.max_opinions:
            break
    if batch:
        flush(batch)
    C.rebuild_caselaw_indexes(os.path.join(a.out, "case-law"), touched, courts, f"snapshot {cdate} + daily API updates")
    stats["api_calls"] = api.calls
    ref = os.path.join(a.out, "references"); os.makedirs(ref, exist_ok=True)
    json.dump({"since": since, "at": run_at, "stats": dict(stats)},
              open(os.path.join(ref, "ingest-stats-case-law-daily.json"), "w"), indent=1)
    print("done:", json.dumps(dict(stats)), flush=True)

if __name__ == "__main__":
    main()

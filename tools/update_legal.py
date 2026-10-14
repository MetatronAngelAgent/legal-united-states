#!/usr/bin/env python3
"""
update_legal.py — the daily job: keep the Legal United States corpus current across all three
branches of government, then write a dated change report.

  EXECUTIVE
    presidential-documents  new Executive Orders, Proclamations, Memoranda, Notices, Determinations
    federal-register        every Federal Register issue since the last run (rules, proposed rules,
                            notices, presidential documents)
    cfr                     CFR titles amended since the last run are re-fetched and rebuilt
  LEGISLATIVE
    congress                new/changed bill text, bill status (actions, summaries) and public laws
                            for the current Congress
    us-code                 rebuilds the U.S. Code (and the federal court rules in its appendices)
                            when the Law Revision Counsel publishes a new release point
    california-codes        weekly rebuild of all 29 California codes from leginfo's newest dump
  JUDICIAL
    court-opinions          opinions filed or revised since the last run, every U.S. court
                            (CourtListener API; needs COURTLISTENER_TOKEN)
    case-law-snapshot       weekly check for a new quarterly CourtListener bulk snapshot; when one
                            appears, its new opinions are ingested in a separate background process
  STATES
    state-law               weekly check of the Open US Law (Vaquill) release; a new revision
                            rebuilds the `states` layer in a background process

Every feed keeps its cursor in <root>/references/state/update-state.json, so a missed day (laptop
asleep, offline) is caught up on the next run. One failing feed never stops the others.
Rebuilt layers are built next to the old copy and swapped in only on success, with a
section-level diff (new / changed / removed) recorded in the report.

Output: <root>/references/updates/<YYYY-MM-DD>.md (OKF Report concept), updates/index.md,
        updates/latest.json, and a log on stdout.

Usage
  python3 update_legal.py                                   # all feeds that are due
  python3 update_legal.py --feeds presidential-documents,congress
  python3 update_legal.py --dry-run                         # show what is due, change nothing
  python3 update_legal.py --force-weekly                    # run weekly feeds today
Schedule it with install_daily.sh (macOS launchd) or cron. Stdlib only.
"""
import argparse, datetime as dt, json, os, platform, re, shutil, subprocess, sys, time, traceback, urllib.request, urllib.error
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
UA = "OKF-LawCorpus/1.0 (open legal-knowledge research; contact " + os.environ.get(
    "LEGAL_US_CONTACT", "see github.com/MetatronAngelAgent/legal-united-states") + ")"
TOOL = "update_legal/1.0"

FEEDS = [  # (branch, feed, cadence)
    ("executive", "presidential-documents", "daily"),
    ("executive", "federal-register", "daily"),
    ("executive", "cfr", "daily"),
    ("legislative", "congress", "daily"),
    ("legislative", "us-code", "daily"),
    ("legislative", "california-codes", "weekly"),
    ("judicial", "court-opinions", "daily"),
    ("judicial", "case-law-snapshot", "weekly"),
    ("states", "state-law", "weekly"),
]
BRANCH_TITLES = {"executive": "Executive branch", "legislative": "Legislative branch",
                 "judicial": "Judicial branch", "states": "State law"}

def log(msg):
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)

def get(url, dest=None, tries=4, method="GET", timeout=1800):
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA}, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if method == "HEAD":
                    return dict(r.headers)
                if dest is None:
                    return r.read()
                with open(dest + ".part", "wb") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
                os.replace(dest + ".part", dest)
                return dest
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 410) or a == tries - 1:
                raise
            time.sleep(15 * (a + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if a == tries - 1:
                raise
            time.sleep(15 * (a + 1))

def tool(args, timeout=None):
    """Run a converter in this directory; raise with its last output lines on failure."""
    cmd = [sys.executable] + [str(x) for x in args]
    log("  $ " + " ".join(str(x) for x in args))
    p = subprocess.run(cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
    tail = "\n".join(p.stdout.strip().splitlines()[-6:])
    for line in tail.splitlines():
        log("    " + line)
    if p.returncode != 0:
        raise RuntimeError(f"{args[0]} exited {p.returncode}: {tail[-600:]}")
    return p.stdout

def read_jsonl(path):
    out = []
    if os.path.exists(path):
        for l in open(path, encoding="utf-8"):
            l = l.strip()
            if l:
                try: out.append(json.loads(l))
                except ValueError: pass
    return out

# ----------------------------------------------------------------------------- tree swap + diff
def checksums(base):
    """relative path -> checksum line, for every concept under base."""
    out = {}
    if not os.path.isdir(base):
        return out
    for dp, _, fs in os.walk(base):
        for f in fs:
            if not f.endswith(".md") or f == "index.md":
                continue
            p = os.path.join(dp, f)
            try:
                head = open(p, encoding="utf-8", errors="replace").read(6000)
            except OSError:
                continue
            m = re.search(r'^checksum: "?([0-9a-f]{16,})', head, re.M)
            t = re.search(r'^title: "(.*)"$', head, re.M)
            out[os.path.relpath(p, base)] = (m.group(1) if m else str(len(head)), t.group(1) if t else f)
    return out

def rebuild_swap(dirs, build):
    """Move each dir aside, run build(), diff old vs new, delete the old copy; restore on failure."""
    stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    moved = []
    before = {}
    for d in dirs:
        before[d] = checksums(d)
        if os.path.isdir(d):
            old = os.path.join(os.path.dirname(d), f".{os.path.basename(d)}-old-{stamp}")
            os.replace(d, old)
            moved.append((d, old))
    try:
        build()
    except Exception:
        for d, old in moved:
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
            os.replace(old, d)
        raise
    diff = {"new": [], "changed": [], "removed": []}
    for d in dirs:
        after = checksums(d)
        rel = lambda p: os.path.join(os.path.basename(d), p)
        for p, (c, t) in after.items():
            if p not in before[d]:
                diff["new"].append((rel(p), t))
            elif before[d][p][0] != c:
                diff["changed"].append((rel(p), t))
        for p, (c, t) in before[d].items():
            if p not in after:
                diff["removed"].append((rel(p), t))
        if not os.path.isdir(d) and before[d]:
            raise RuntimeError(f"rebuild produced no {d}")
    for _, old in moved:
        shutil.rmtree(old, ignore_errors=True)
    return diff

# ----------------------------------------------------------------------------- feeds
class Ctx:
    def __init__(self, a, state):
        self.root, self.cache, self.a, self.state = a.root, a.cache, a, state
        self.today = dt.date.today()
        self.items = defaultdict(list)       # feed -> [ {path,title,...} ]
        self.notes = defaultdict(list)       # feed -> [str]
        self.report_dir = os.path.join(self.root, "references", "updates")
        self.scratch = os.path.join(self.cache, "update-run")
    def report_file(self, feed):
        os.makedirs(self.scratch, exist_ok=True)
        p = os.path.join(self.scratch, f"{feed}.jsonl")
        if os.path.exists(p): os.remove(p)
        return p
    def fs(self, feed):
        return self.state.setdefault("feeds", {}).setdefault(feed, {})

def build_state(root):
    p = os.path.join(root, "references", "build-state.json")
    return json.load(open(p)) if os.path.exists(p) else {"done": {}}

def f_presidential(c):
    s = c.fs("presidential-documents")
    last = s.get("through")
    since = (dt.date.fromisoformat(last) - dt.timedelta(days=7)) if last else (c.today - dt.timedelta(days=30))
    rp = c.report_file("presidential-documents")
    tool(["presdocs_to_okf.py", "--out", c.root, "--since", since.isoformat(), "--report", rp])
    c.items["presidential-documents"] += read_jsonl(rp)
    if not last and not os.path.isdir(os.path.join(c.root, "federal", "executive", "presidential-documents")):
        c.notes["presidential-documents"].append("Backfill 1994-present with: build_legal.py --layers presidential")
    s["through"] = c.today.isoformat()

def f_federal_register(c):
    s = c.fs("federal-register")
    last = dt.date.fromisoformat(s["through"]) if s.get("through") else c.today - dt.timedelta(days=7)
    d = last + dt.timedelta(days=1)
    rp = c.report_file("federal-register")
    days, through = [], last
    while d <= c.today:
        if d.weekday() < 5:
            src = os.path.join(c.cache, "fr-daily", d.isoformat())
            os.makedirs(src, exist_ok=True)
            fn = os.path.join(src, f"FR-{d.isoformat()}.xml")
            try:
                if not os.path.exists(fn):
                    get(f"https://www.govinfo.gov/bulkdata/FR/{d:%Y}/{d:%m}/FR-{d.isoformat()}.xml", fn, timeout=600)
                tool(["fr_to_okf.py", "--src", src, "--out", c.root, "--report", rp])
                days.append(d.isoformat())
                shutil.rmtree(src, ignore_errors=True)
            except urllib.error.HTTPError as e:
                shutil.rmtree(src, ignore_errors=True)
                if e.code != 404:
                    raise
                if d == c.today:
                    c.notes["federal-register"].append(f"{d} issue not posted yet; will be picked up next run")
                    break                                   # do not advance past today's missing issue
                # past weekday with no issue = federal holiday
        through = d
        d += dt.timedelta(days=1)
    c.items["federal-register"] += read_jsonl(rp)
    if days:
        c.notes["federal-register"].append("Issues converted: " + ", ".join(days))
    s["through"] = through.isoformat()

def f_cfr(c):
    s = c.fs("cfr")
    meta = json.loads(get("https://www.ecfr.gov/api/versioner/v1/titles.json"))
    cur = {str(t["number"]): f"{t.get('latest_amended_on')}|{t.get('latest_issue_date')}"
           for t in meta["titles"] if not t.get("reserved")}
    seen = s.get("titles")
    s["as_of"] = (meta.get("meta") or {}).get("date")
    if seen is None:
        s["titles"] = cur
        c.notes["cfr"].append("Baseline recorded (first run); titles amended from now on will be rebuilt.")
        return
    changed = sorted((n for n, v in cur.items() if seen.get(n) != v), key=int)
    if not changed:
        c.notes["cfr"].append(f"No CFR title amended (eCFR current as of {s['as_of']}).")
        return
    src = os.path.join(c.cache, "cfr-update")
    os.makedirs(src, exist_ok=True)
    for n in changed:
        fp = os.path.join(src, f"title-{n}.xml")
        if os.path.exists(fp): os.remove(fp)
    tool(["ecfr_fetch.py", "--out", src, "--titles", ",".join(changed)])
    cfr = os.path.join(c.root, "federal", "cfr")
    for n in changed:
        diff = rebuild_swap([os.path.join(cfr, f"title-{n}")],
                            lambda n=n: tool(["ecfr_to_okf.py", "--src", src, "--out", c.root, "--titles", n]))
        c.notes["cfr"].append(f"Title {n} (amended {cur[n].split('|')[0]}): {len(diff['new'])} new, "
                              f"{len(diff['changed'])} changed, {len(diff['removed'])} removed sections")
        for k in ("new", "changed", "removed"):
            for p, t in diff[k]:
                c.items["cfr"].append({"branch": "executive", "feed": "cfr", "path": f"federal/cfr/{p}", "title": t, "change": k})
        seen[n] = cur[n]
        os.remove(os.path.join(src, f"title-{n}.xml"))
    s["titles"] = seen

def f_congress(c):
    import govinfo_leg_to_okf as G
    s = c.fs("congress")
    cur = G.current_congress(c.today)
    rp = c.report_file("congress")
    tool(["govinfo_leg_to_okf.py", "--out", c.root, "--congress", str(cur), "--report", rp])
    if s.get("congress") and int(s["congress"]) != cur:   # new Congress: pick up the old one's last changes
        tool(["govinfo_leg_to_okf.py", "--out", c.root, "--congress", str(s["congress"]), "--report", rp])
    c.items["congress"] += read_jsonl(rp)
    if not s.get("congress"):
        c.notes["congress"].append(f"Initial load of the {cur}th Congress (bulk zips); later runs fetch only changed files.")
    s["congress"] = cur
    s["through"] = c.today.isoformat()

def f_us_code(c):
    s = c.fs("us-code")
    page = get("https://uscode.house.gov/download/download.shtml").decode("utf-8", "replace")
    m = re.search(r"current through Public Law (\d+)-(\d+)", page)
    if not m:
        raise RuntimeError("could not read the current release point from uscode.house.gov")
    rel = f"{m.group(1)}-{m.group(2)}"
    built = s.get("release") or ((build_state(c.root)["done"].get("usc") or {}).get("result") or {})
    built = built.get("release") if isinstance(built, dict) else built
    if not built:
        s["release"] = rel
        c.notes["us-code"].append(f"Baseline recorded: release point {rel}.")
        return
    if built == rel:
        c.notes["us-code"].append(f"U.S. Code unchanged (release point {rel}).")
        return
    fed = os.path.join(c.root, "federal")
    def build():
        tool(["build_legal.py", "--root", c.root, "--cache", c.cache, "--layers", "usc,court-rules",
              "--force", "usc,court-rules", "--usc-release", rel])
    diff = rebuild_swap([os.path.join(fed, "usc"), os.path.join(fed, "court-rules")], build)
    for k in ("new", "changed", "removed"):
        for p, t in diff[k]:
            c.items["us-code"].append({"branch": "legislative", "feed": "us-code", "path": f"federal/{p}", "title": t, "change": k})
    c.notes["us-code"].append(f"New release point {rel} (was {built}): {len(diff['new'])} new, {len(diff['changed'])} changed, "
                              f"{len(diff['removed'])} removed sections (U.S. Code + court rules).")
    s["release"] = rel

def f_california(c):
    s = c.fs("california-codes")
    idx = get("https://downloads.leginfo.legislature.ca.gov/").decode("latin-1")
    names = sorted(set(re.findall(r"pubinfo_daily_[A-Za-z]{3}\.zip", idx)))
    best = None
    for n in names:
        h = get(f"https://downloads.leginfo.legislature.ca.gov/{n}", method="HEAD", timeout=120)
        lm = h.get("Last-Modified") or h.get("last-modified")
        t = dt.datetime.strptime(lm, "%a, %d %b %Y %H:%M:%S %Z") if lm else dt.datetime.min
        if not best or t > best[1]:
            best = (n, t)
    if not best:
        raise RuntimeError("no pubinfo_daily dump listed")
    stamp = best[1].strftime("%Y-%m-%dT%H:%M")
    if s.get("dump") == stamp:
        c.notes["california-codes"].append("No newer leginfo dump since the last rebuild.")
        return
    fn = os.path.join(c.cache, best[0])
    log(f"  downloading {best[0]} ({stamp}, ~1.2 GB)")
    get(f"https://downloads.leginfo.legislature.ca.gov/{best[0]}", fn)
    try:
        diff = rebuild_swap([os.path.join(c.root, "california", "codes")],
                            lambda: tool(["pubinfo_to_okf.py", "--dump", fn, "--out", c.root]))
    finally:
        if os.path.exists(fn): os.remove(fn)
    for k in ("new", "changed", "removed"):
        for p, t in diff[k]:
            c.items["california-codes"].append({"branch": "legislative", "feed": "california-codes", "path": f"california/{p}", "title": t, "change": k})
    c.notes["california-codes"].append(f"Rebuilt from {best[0]} ({stamp} UTC): {len(diff['new'])} new, "
                                       f"{len(diff['changed'])} changed, {len(diff['removed'])} repealed/removed sections.")
    s["dump"] = stamp

def f_court_opinions(c):
    s = c.fs("court-opinions")
    token = os.environ.get("COURTLISTENER_TOKEN", "")
    if not token:
        c.notes["court-opinions"].append("Skipped: set COURTLISTENER_TOKEN (free API token at courtlistener.com → "
                                         "Profile → Developer Tools) in ~/.config/legal-united-states/env to get daily opinions.")
        return
    started = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    since = s.get("since") or (started - dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rp = c.report_file("court-opinions")
    scope = (build_state(c.root)["done"].get("case-law") or {}).get("result")
    scope = scope.get("scope", "all") if isinstance(scope, dict) else "all"
    tool(["cl_api_to_okf.py", "--out", c.root, "--since", since, "--scope", scope, "--report", rp,
          "--cache", os.path.join(c.cache, "courtlistener")])
    c.items["court-opinions"] += read_jsonl(rp)
    # overlap by an hour: opinions modified during the run are fetched again next time (idempotent)
    s["since"] = (started - dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

def spawn_background(c, feed, args):
    """Long rebuilds (hours) run detached so they never block tomorrow's daily feeds."""
    lock = os.path.join(c.root, "references", "state", f"{feed}.lock")
    if os.path.exists(lock) and time.time() - os.path.getmtime(lock) < 3 * 86400:
        c.notes[feed].append("Background rebuild already running (lock present).")
        return False
    logd = os.path.join(c.root, "references", "logs"); os.makedirs(logd, exist_ok=True)
    logf = os.path.join(logd, f"{feed}-{c.today}.log")
    sh = (f'touch "{lock}"; "{sys.executable}" "{os.path.join(HERE, args[0])}" ' + " ".join(f'"{x}"' for x in args[1:]) +
          f' > "{logf}" 2>&1; rm -f "{lock}"')
    subprocess.Popen(["/bin/sh", "-c", sh], cwd=HERE, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    c.notes[feed].append(f"Started in the background; log: references/logs/{os.path.basename(logf)}")
    return True

def f_case_law_snapshot(c):
    s = c.fs("case-law-snapshot")
    done = build_state(c.root)["done"].get("case-law")
    if not done:
        c.notes["case-law-snapshot"].append("Case-law layer not built yet (build_legal.py --layers case-law); nothing to refresh.")
        return
    res = done.get("result") if isinstance(done.get("result"), dict) else {}
    built = s.get("snapshot") or res.get("snapshot") or "2026-09-30"
    keys = get("https://com-courtlistener-storage.s3-us-west-2.amazonaws.com/?list-type=2&prefix=bulk-data/opinions-").decode()
    snaps = sorted(set(re.findall(r"bulk-data/opinions-(\d{4}-\d{2}-\d{2})\.csv\.bz2", keys)))
    newest = snaps[-1] if snaps else built
    if newest <= built:
        c.notes["case-law-snapshot"].append(f"No new quarterly snapshot (current {built}).")
        return
    if spawn_background(c, "case-law-snapshot", ["build_legal.py", "--root", c.root, "--cache", c.cache, "--layers", "case-law",
                                                 "--force", "case-law", "--cl-date", newest,
                                                 "--caselaw-scope", res.get("scope", "all"), "--allow-icloud"]):
        c.notes["case-law-snapshot"].append(f"New CourtListener snapshot {newest} (was {built}); opinions already present are skipped.")
        s["snapshot"] = newest

def f_state_law(c):
    s = c.fs("state-law")
    if not build_state(c.root)["done"].get("states"):
        c.notes["state-law"].append("`states` layer not built yet; nothing to refresh.")
        return
    j = json.loads(get("https://huggingface.co/api/datasets/vaquill/open-us-law"))
    sha, mod = j.get("sha"), j.get("lastModified")
    if not s.get("sha"):
        s["sha"], s["modified"] = sha, mod
        c.notes["state-law"].append(f"Baseline recorded: Open US Law revision {str(sha)[:10]} ({mod}).")
        return
    if s["sha"] == sha:
        c.notes["state-law"].append(f"Open US Law unchanged (revision {str(sha)[:10]}, {mod}).")
        return
    if spawn_background(c, "state-law", ["build_legal.py", "--root", c.root, "--cache", c.cache, "--layers", "states",
                                         "--force", "states", "--allow-icloud"]):
        c.notes["state-law"].append(f"New Open US Law revision {str(sha)[:10]} ({mod}); `states` layer rebuilding.")
        s["sha"], s["modified"] = sha, mod

RUN = {"presidential-documents": f_presidential, "federal-register": f_federal_register, "cfr": f_cfr,
       "congress": f_congress, "us-code": f_us_code, "california-codes": f_california,
       "court-opinions": f_court_opinions, "case-law-snapshot": f_case_law_snapshot, "state-law": f_state_law}

# ----------------------------------------------------------------------------- report
def yq(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'

def link(p):
    return "/" + p.lstrip("/")

def summarize(feed, items):
    """Human lines for one feed's items (capped; full lists live in latest.json)."""
    lines = []
    if feed == "presidential-documents":
        for it in items:
            lines.append(f"- [{it['title']}]({link(it['path'])})")
    elif feed == "federal-register":
        by = Counter(it.get("kind", "") for it in items)
        lines.append("- " + ", ".join(f"{n} {k.replace('-', ' ')}s" for k, n in sorted(by.items())))
        rules = [it for it in items if it.get("kind") == "rule"][:60]
        if rules:
            lines.append("\n**Final rules**\n")
            lines += [f"- [{it['title']}]({link(it['path'])})" for it in rules]
    elif feed == "congress":
        laws = [it for it in items if it["feed"] == "public-laws"]
        status = [it for it in items if it["feed"] == "bill-status"]
        text = [it for it in items if it["feed"] == "bill-text"]
        lines.append(f"- {len(laws)} public/private laws, {len(status)} bills with new actions or status, {len(text)} new bill texts")
        if laws:
            lines.append("\n**Laws**\n"); lines += [f"- [{it['title']}]({link(it['path'])})" for it in laws]
        if status:
            lines.append("\n**Bill activity**" + (" (first 75)" if len(status) > 75 else "") + "\n")
            lines += [f"- [{it['title']}]({link(it['path'])})" for it in status[:75]]
    elif feed == "court-opinions":
        by = Counter(it["path"].split("/")[1] for it in items)
        lines.append("- " + ", ".join(f"{k}: {n}" for k, n in by.most_common()))
        top = [it for it in items if "/scotus/" in it["path"] or "/federal/ca" in it["path"]][:60]
        if top:
            lines.append("\n**Supreme Court and courts of appeals**\n")
            lines += [f"- [{it['title']}]({link(it['path'])})" for it in top]
    else:
        by = Counter(it.get("change", "") for it in items)
        lines.append("- " + ", ".join(f"{n} {k}" for k, n in by.items()))
        lines += [f"- {it.get('change', '')}: [{it['title']}]({link(it['path'])})" for it in items[:60]]
    return lines

def write_report(c, results, started):
    os.makedirs(c.report_dir, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total = sum(len(v) for v in c.items.values())
    failed = [f for f, r in results.items() if r["status"] == "error"]
    md = ["---", 'type: "Report"', f"title: {yq(f'Corpus update {c.today}')}",
          f"description: {yq(f'{total} new or changed concepts across the executive, legislative and judicial feeds' + (f'; {len(failed)} feed(s) failed' if failed else ''))}",
          'tags: ["report", "update", "daily"]', f'generated: {{ by: "{TOOL}", at: "{now}" }}', "---", "",
          f"# Corpus update — {c.today}", "",
          f"Run {started:%H:%M}–{dt.datetime.now():%H:%M} local. {total} new or changed concepts." +
          (f" **Failed:** {', '.join(failed)}." if failed else "") + " " + getattr(c, "check", ""), ""]
    for branch in ("executive", "legislative", "judicial", "states"):
        md += [f"## {BRANCH_TITLES[branch]}", ""]
        for b, feed, _ in FEEDS:
            if b != branch:
                continue
            r = results.get(feed)
            if not r:
                continue
            items = c.items.get(feed, [])
            head = {"ok": f"{len(items)} new/changed", "skipped": "not due", "error": "FAILED"}[r["status"]]
            md += [f"### {feed} — {head}", ""]
            if r["status"] == "error":
                md += ["```", r["error"][-1500:], "```", ""]
            md += [f"- {n}" for n in c.notes.get(feed, [])]
            if items:
                md += summarize(feed, items)
            md.append("")
    path = os.path.join(c.report_dir, f"{c.today}.md")
    if os.path.exists(path):                       # a second run the same day keeps the first report
        path = os.path.join(c.report_dir, f"{c.today}-{dt.datetime.now():%H%M}.md")
    open(path, "w", encoding="utf-8").write("\n".join(md) + "\n")
    json.dump({"date": str(c.today), "results": results, "notes": c.notes, "items": c.items},
              open(os.path.join(c.report_dir, "latest.json"), "w"), indent=1, default=str)
    reps = sorted((f for f in os.listdir(c.report_dir) if re.fullmatch(r"\d{4}-\d{2}-\d{2}(-\d{4})?\.md", f)), reverse=True)
    open(os.path.join(c.report_dir, "index.md"), "w", encoding="utf-8").write(
        "# Daily corpus updates\n\nWhat changed in the law each day, by branch of government.\n\n" +
        "\n".join(f"* [{f[:-3]}]({f})" for f in reps) + "\n")
    return path, total, failed

def check_new_files(root, items):
    """Light OKF check of every concept written this run (full validation is okf_validate.py)."""
    bad = []
    for it in items:
        p = os.path.join(root, it["path"])
        if it.get("change") == "removed" or not os.path.exists(p):
            continue
        head = open(p, encoding="utf-8", errors="replace").read(4000)
        if not head.startswith("---\n") or not re.search(r"^type: ", head, re.M) or not re.search(r"^title: ", head, re.M):
            bad.append(it["path"])
    return bad

def notify(title, msg):
    if platform.system() == "Darwin" and shutil.which("osascript"):
        subprocess.run(["osascript", "-e", f'display notification {json.dumps(msg)} with title {json.dumps(title)}'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ----------------------------------------------------------------------------- main
def load_env():
    p = os.path.expanduser("~/.config/legal-united-states/env")
    if os.path.exists(p):
        for l in open(p):
            m = re.match(r"\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*['\"]?([^'\"\n]*)", l)
            if m and m.group(1) not in os.environ:
                os.environ[m.group(1)] = m.group(2)

def main():
    load_env()
    ap = argparse.ArgumentParser(description="Daily update of the Legal United States corpus")
    ap.add_argument("--root", default=os.environ.get("LEGAL_US_ROOT", os.path.expanduser("~/Documents/OpenKnowledge/Cuh/legal")))
    ap.add_argument("--cache", default=os.path.expanduser(
        "~/Library/Caches/cuh-legal" if platform.system() == "Darwin" else "~/.cache/cuh-legal"))
    ap.add_argument("--feeds", default="all")
    ap.add_argument("--force-weekly", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    a.root, a.cache = os.path.abspath(a.root), os.path.abspath(a.cache)
    if not os.path.isdir(a.root):
        raise SystemExit(f"corpus root not found: {a.root} (build it first with build_legal.py, or pass --root)")
    st_dir = os.path.join(a.root, "references", "state"); os.makedirs(st_dir, exist_ok=True)
    os.makedirs(a.cache, exist_ok=True)
    lock = os.path.join(st_dir, "update.lock")
    if os.path.exists(lock) and time.time() - os.path.getmtime(lock) < 12 * 3600:
        raise SystemExit(f"another update is running (lock {lock}); delete it if that run died")
    sp = os.path.join(st_dir, "update-state.json")
    state = json.load(open(sp)) if os.path.exists(sp) else {"feeds": {}}
    c = Ctx(a, state)
    want = [f for _, f, _ in FEEDS] if a.feeds == "all" else [x.strip() for x in a.feeds.split(",")]
    unknown = [f for f in want if f not in RUN]
    if unknown:
        raise SystemExit(f"unknown feed(s) {unknown}; choose from {[f for _, f, _ in FEEDS]}")
    due = []
    for b, f, cad in FEEDS:
        if f not in want:
            continue
        last = c.fs(f).get("last_run")
        if cad == "weekly" and not a.force_weekly and a.feeds == "all" and last and \
                (c.today - dt.date.fromisoformat(last[:10])).days < 7:
            continue
        due.append(f)
    log(f"root {a.root}")
    log("due: " + ", ".join(due))
    if a.dry_run:
        return
    open(lock, "w").write(str(os.getpid()))
    started = dt.datetime.now()
    results = {f: {"status": "skipped"} for _, f, _ in FEEDS if f in want}
    try:
        for f in due:
            log(f"=== {f}")
            t0 = time.time()
            try:
                RUN[f](c)
                results[f] = {"status": "ok", "seconds": round(time.time() - t0), "count": len(c.items.get(f, []))}
                c.fs(f)["last_run"] = dt.datetime.now().isoformat(timespec="seconds")
                c.fs(f).pop("last_error", None)
            except Exception as e:
                results[f] = {"status": "error", "seconds": round(time.time() - t0), "error": f"{e}\n{traceback.format_exc()[-1200:]}"}
                c.fs(f)["last_error"] = f"{dt.datetime.now().isoformat(timespec='seconds')}: {e}"
                log(f"  FAILED: {e}")
            json.dump(state, open(sp + ".part", "w"), indent=1); os.replace(sp + ".part", sp)
            log(f"=== {f}: {results[f]['status']} ({results[f].get('count', 0)} items, {results[f]['seconds']}s)")
        bad = check_new_files(a.root, [it for v in c.items.values() for it in v])
        c.check = (f"{len(bad)} written files failed the frontmatter check, e.g. {bad[:3]}" if bad else
                   "All files written this run passed the frontmatter check.")
        try:
            import build_legal
            build_legal.write_root(a.root, build_state(a.root)["done"])
        except Exception as e:
            log(f"  root index not rewritten: {e}")
        path, total, failed = write_report(c, results, started)
        log(f"report: {path}")
        notify("Legal corpus updated", f"{total} new/changed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    finally:
        if os.path.exists(lock): os.remove(lock)
    sys.exit(1 if any(r["status"] == "error" for r in results.values()) else 0)

if __name__ == "__main__":
    main()

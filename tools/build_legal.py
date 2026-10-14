#!/usr/bin/env python3
"""
build_legal.py — build (or refresh) the U.S. law layer of the Cuh knowledge base.

Creates an Open Knowledge Format (OKF v0.2) bundle at

    ~/Documents/OpenKnowledge/Cuh/legal/

from official / openly-licensed sources, one layer at a time, resumable. The converter
scripts live next to this file (in legal/references/tools/ once installed). Downloads are
cached OUTSIDE Documents (~/Library/Caches/cuh-legal on macOS) so iCloud never sees them.

Layers (run in this order; later ones link to earlier ones):
  constitution  U.S. Constitution (National Archives)                         ~1 MB
  usc           United States Code, all titles (OLRC release point XML)      ~0.9 GB
  court-rules   FRAP, FRCP, FRE, FRCrP, FRBP (USC appendix titles)           ~3 MB
  cfr           Code of Federal Regulations (govinfo eCFR bulk XML)          ~1.6 GB
  ca-codes      California Constitution + 29 codes (leginfo pubinfo)         ~0.6 GB
  colorado      Colorado Revised Statutes, official OLLS annual release      ~0.3 GB
  states        All other states + DC + PR: statutes, constitutions, regs,
                court rules, AG opinions (Vaquill Open US Law, CC BY-NC 4.0) ~14 GB
  gap-states    Arkansas, Georgia, Mississippi, Tennessee statutes — newest public-domain
                bulk releases (Public.Resource.Org/UniCourt on GitHub; 2020-2022 snapshots) ~1 GB
  san-diego     San Diego Municipal Code (City Clerk PDFs; needs pdftotext)  ~40 MB
  ca-manifest   Every CA city/county -> municipal code publisher             ~1 MB
  fr            Federal Register, 2000–present (govinfo bulk XML)            ~15 GB
  agency        Agency decisions/opinions/guidance (Pile of Law, CC BY-NC-SA) ~6 GB
  bva           Board of Veterans' Appeals decisions (Pile of Law)           ~15 GB
  case-law      ALL U.S. case law — every federal, state, territorial, tribal and military
                court, 1658-present (CourtListener quarterly bulk data, public domain)
                ~200 GB for --caselaw-scope all (default); e.g. federal,california for less
  eval          LegalBench evaluation set (CC BY 4.0)                        ~80 MB

Usage:
  python3 build_legal.py --dry-run                       # plan, disk + iCloud checks
  python3 build_legal.py                                 # everything (hours; resumable)
  python3 build_legal.py --layers usc,cfr,case-law       # selected layers
  python3 build_legal.py --fr-from 2020 --caselaw-scope federal,california
  python3 build_legal.py --force usc                     # rebuild a finished layer
"""
import argparse, datetime as dt, json, os, platform, re, shutil, subprocess, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "OKF-LawCorpus/1.0 (open legal-knowledge research; contact " + os.environ.get("LEGAL_US_CONTACT", "see github.com/MetatronAngelAgent/legal-united-states") + ")"
ORDER = ["constitution", "usc", "court-rules", "cfr", "ca-codes", "colorado", "states", "gap-states", "san-diego", "ca-manifest",
         "fr", "agency", "bva", "case-law", "eval"]
EST_GB = {"constitution": 0.01, "usc": 1.0, "court-rules": 0.01, "cfr": 2.0, "ca-codes": 0.8,
          "san-diego": 0.1, "ca-manifest": 0.01, "fr": 16, "agency": 7, "bva": 16, "case-law": 200, "eval": 0.1, "colorado": 0.3, "states": 14, "gap-states": 1.0, "case-law-cap": 24}
DL_GB = {"gap-states": 0.7, "states": 0.3, "colorado": 0.1, "usc": 0.2, "cfr": 1.0, "ca-codes": 1.3, "fr": 1.0, "agency": 1.0, "bva": 2.3, "case-law": 4.0, "case-law-cap": 0.4}  # peak cache

def log(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)

def get(url, dest=None, tries=4, headers=None):
    h = {"User-Agent": UA}
    h.update(headers or {})
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=1800) as r:
                if dest is None:
                    return r.read()
                with open(dest + ".part", "wb") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
                os.replace(dest + ".part", dest)
                return dest
        except Exception as e:
            if a == tries - 1:
                raise
            log(f"  retry {a + 1} for {url}: {e}")
            time.sleep(15 * (a + 1))

def run(args, cwd=None):
    log("  $ " + " ".join(str(x) for x in args))
    r = subprocess.run([sys.executable] + [str(x) for x in args], cwd=cwd or HERE)
    if r.returncode != 0:
        raise SystemExit(f"step failed ({r.returncode}): {' '.join(str(x) for x in args)}")

def free_gb(path):
    p = path
    while not os.path.exists(p):
        p = os.path.dirname(p)
    return shutil.disk_usage(p).free / 1e9

def icloud_synced(path):
    """True if ~/Documents is under iCloud Drive's 'Desktop & Documents' sync."""
    if platform.system() != "Darwin":
        return False
    real = os.path.realpath(path)
    if "Mobile Documents" in real or "CloudDocs" in real:
        return True
    try:
        out = subprocess.run(["xattr", "-l", os.path.expanduser("~/Documents")],
                             capture_output=True, text=True).stdout
        return "com.apple.icloud" in out.lower() or "com.apple.file-provider" in out.lower()
    except Exception:
        return False

# ----------------------------------------------------------------------------- layers
def L_constitution(root, cache, a):
    d = os.path.join(cache, "nara"); os.makedirs(d, exist_ok=True)
    for p in ("constitution-transcript", "bill-of-rights-transcript", "amendments-11-27"):
        get(f"https://www.archives.gov/founding-docs/{p}", os.path.join(d, p + ".html"))
    run(["us_constitution_to_okf.py", "--src", d, "--out", root])

def usc_zip(cache, a):
    rel = a.usc_release
    fn = os.path.join(cache, f"xml_uscAll@{rel}.zip")
    if not os.path.exists(fn):
        congress, law = rel.split("-")
        get(f"https://uscode.house.gov/download/releasepoints/us/pl/{congress}/{law}/xml_uscAll@{rel}.zip", fn)
    return fn

def L_usc(root, cache, a):
    run(["uslm_to_okf.py", "--dump", usc_zip(cache, a), "--out", root])

def L_court_rules(root, cache, a):
    run(["uslm_rules_to_okf.py", "--dump", usc_zip(cache, a), "--out", root])

def L_cfr(root, cache, a):
    d = os.path.join(cache, "ecfr")
    run(["ecfr_fetch.py", "--out", d])
    run(["ecfr_to_okf.py", "--src", d, "--out", root])

def L_ca_codes(root, cache, a):
    idx = get("https://downloads.leginfo.legislature.ca.gov/").decode("latin-1")
    years = sorted(int(y) for y in re.findall(r"pubinfo_(\d{4})\.zip", idx))
    y = years[-1]
    fn = os.path.join(cache, f"pubinfo_{y}.zip")
    if not os.path.exists(fn):
        log(f"  downloading pubinfo_{y}.zip (~1.2 GB)")
        get(f"https://downloads.leginfo.legislature.ca.gov/pubinfo_{y}.zip", fn)
    run(["pubinfo_to_okf.py", "--dump", fn, "--out", root])
    if not a.keep_cache:
        os.remove(fn)

def L_colorado(root, cache, a):
    run(["co_crs_to_okf.py", "--year", str(dt.date.today().year), "--cache", os.path.join(cache, "co"), "--out", root])

def L_states(root, cache, a):
    log("  NOTE: state layer from Vaquill Open US Law (compilation CC BY-NC 4.0 = non-commercial). "
        "Each concept is tagged; see references/tools/README.md for commercial-clean alternatives.")
    try:
        import pyarrow  # noqa
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "--user", "-q", "pyarrow"])
    run(["vaquill_to_okf.py", "--cache", os.path.join(cache, "vaquill"), "--out", root] +
        (["--keep-cache"] if a.keep_cache else []))

def L_gap_states(root, cache, a):
    log("  NOTE: newest public-domain releases are 2020-2022 snapshots; every concept carries `currency`/`stale_after`.")
    run(["pro_codes_to_okf.py", "--cache", os.path.join(cache, "pro-codes"), "--out", root])

def L_san_diego(root, cache, a):
    if not shutil.which("pdftotext"):
        log("  SKIP san-diego: needs pdftotext — install with:  brew install poppler   then rerun --layers san-diego")
        return "skipped"
    d = os.path.join(cache, "sdmc")
    run(["sdmc_fetch.py", "--cache", d])
    run(["sdmc_to_okf.py", "--cache", d, "--out", root])

def L_ca_manifest(root, cache, a):
    d = os.path.join(cache, "muni"); os.makedirs(d, exist_ok=True)
    get("https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/cities/totals/sub-est2024.csv",
        os.path.join(d, "subest.csv"))
    get("https://www.icccodesolutions.org/text-library/", os.path.join(d, "icc.html"))
    get("https://api.municode.com/Clients/stateAbbr?stateAbbr=ca", os.path.join(d, "municode.json"),
        headers={"Accept": "application/json"})
    run(["ca_municipal_manifest.py", "--census", os.path.join(d, "subest.csv"), "--icc", os.path.join(d, "icc.html"),
         "--municode", os.path.join(d, "municode.json"), "--out", root])

def L_fr(root, cache, a):
    d = os.path.join(cache, "fr")
    this_year = dt.date.today().year
    for y in range(a.fr_from, this_year + 1):
        yd = os.path.join(d, str(y)); os.makedirs(yd, exist_ok=True)
        marker = os.path.join(root, "references", f".fr-{y}.done")
        if os.path.exists(marker) and y != this_year:
            continue
        run(["fr_to_okf.py", "--src", yd, "--fetch", str(y), "--out", root])
        open(marker, "w").write(dt.datetime.now().isoformat())
        if not a.keep_cache and y != this_year:
            shutil.rmtree(yd, ignore_errors=True)

def L_agency(root, cache, a):
    run(["pol_to_okf.py", "--cache", os.path.join(cache, "pol"), "--out", root, "--fetch"] +
        ([] if a.keep_cache else ["--delete-after"]))

def L_bva(root, cache, a):
    run(["pol_to_okf.py", "--cache", os.path.join(cache, "pol"), "--out", root, "--fetch", "--subsets", "bva"] +
        ([] if a.keep_cache else ["--delete-after"]))

def L_case_law(root, cache, a):
    # CourtListener bulk data: superset of the Caselaw Access Project, current to the last quarter.
    # Opinions (~55 GB compressed) are streamed, never stored, unless --keep-cache. Resumable.
    run(["cl_to_okf.py", "--cache", os.path.join(cache, "courtlistener"), "--out", root,
         "--scope", a.caselaw_scope, "--date", a.cl_date] + (["--keep"] if a.keep_cache else []))

def L_case_law_cap(root, cache, a):
    """Older alternative: Caselaw Access Project only (to 2018). Not in the default order."""
    run(["cap_to_okf.py", "--cache", os.path.join(cache, "cap"), "--out", root,
         "--scope", "all" if a.caselaw_scope == "all" else "ca,federal"] + (["--keep-shards"] if a.keep_cache else []))

def L_eval(root, cache, a):
    dest = os.path.join(root, "eval", "legalbench")
    tree = json.loads(get("https://huggingface.co/api/datasets/nguha/legalbench/tree/main?recursive=1"))
    files = [t["path"] for t in tree if t.get("type") == "file"]
    for p in files:
        out = os.path.join(dest, p)
        if os.path.exists(out):
            continue
        os.makedirs(os.path.dirname(out), exist_ok=True)
        get(f"https://huggingface.co/datasets/nguha/legalbench/resolve/main/{p}", out)
    rd = os.path.join(dest, "README.md")
    s = open(rd, encoding="utf-8").read()
    if s.startswith("---\n") and "\ntype:" not in s.split("---", 2)[1]:
        open(rd, "w", encoding="utf-8").write('---\ntype: "Dataset"\ntitle: "LegalBench"\n' + s[4:])
    src = os.path.join(HERE, "eval-about-legalbench.md")
    if os.path.exists(src):
        shutil.copy(src, os.path.join(root, "eval", "about-legalbench.md"))
    open(os.path.join(root, "eval", "index.md"), "w", encoding="utf-8").write(
        "# Evaluation sets\n\n* [About LegalBench](about-legalbench.md) - what it is and how to use it\n"
        "* [LegalBench dataset](legalbench/README.md) - 162 legal-reasoning tasks (train/test TSVs)\n")

LAYERS = {"colorado": L_colorado, "states": L_states, "constitution": L_constitution, "usc": L_usc, "court-rules": L_court_rules, "cfr": L_cfr,
          "ca-codes": L_ca_codes, "san-diego": L_san_diego, "ca-manifest": L_ca_manifest, "fr": L_fr,
          "agency": L_agency, "bva": L_bva, "case-law": L_case_law, "case-law-cap": L_case_law_cap, "eval": L_eval,
          "gap-states": L_gap_states}

# ----------------------------------------------------------------------------- bundle root
TITLES = {"federal": "Federal law", "california": "California", "case-law": "Case law",
          "eval": "Evaluation sets", "references": "References, reports & tools"}

def tag_reports(root):
    """OKF: every non-reserved .md is a concept and needs frontmatter with `type`."""
    ref = os.path.join(root, "references")
    if not os.path.isdir(ref):
        return
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for fn in os.listdir(ref):
        p = os.path.join(ref, fn)
        if not fn.endswith(".md") or fn in ("index.md", "log.md"):
            continue
        s = open(p, encoding="utf-8").read()
        if s.startswith("---\n"):
            continue
        title = (re.search(r"^#\s+(.+)$", s, re.M) or [None, fn[:-3]])[1]
        fm = (f'---\ntype: "Report"\ntitle: "{title}"\n'
              f'description: "Ingest/validation report for the legal bundle."\n'
              f'tags: ["report", "provenance"]\ngenerated: {{ by: "build_legal/1.0", at: "{now}" }}\n---\n\n')
        open(p, "w", encoding="utf-8").write(fm + s)

def write_root(root, done):
    tag_reports(root)
    kids = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)) and not d.startswith("."))
    fed = os.path.join(root, "federal")
    if os.path.isdir(fed):
        names = {"usc": "United States Code", "constitution": "Constitution", "court-rules": "Federal court rules",
                 "cfr": "Code of Federal Regulations", "federal-register": "Federal Register",
                 "agency": "Agency decisions, opinions & guidance"}
        sub = sorted(d for d in os.listdir(fed) if os.path.isdir(os.path.join(fed, d)))
        open(os.path.join(fed, "index.md"), "w", encoding="utf-8").write(
            "# Federal law\n\n" + "\n".join(f"* [{names.get(d, d)}]({d}/)" for d in sub) + "\n")
    lines = ['---', 'okf_version: "0.2"', '---', '', '# Legal — U.S. law knowledge base', '',
             "Statutes, regulations, court rules, case law and agency decisions as OKF concepts.",
             "Built by `references/tools/build_legal.py`; per-layer reports in `references/`.", '']
    lines += [f"* [{TITLES.get(d, d)}]({d}/)" for d in kids if d != "references"]
    lines += ["", f"* [{TITLES['references']}](references/)"] if "references" in kids else []
    open(os.path.join(root, "index.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    # link from the Cuh root index, if Cuh is itself an OKF bundle with an index.md
    cuh_index = os.path.join(os.path.dirname(root), "index.md")
    if os.path.exists(cuh_index):
        s = open(cuh_index, encoding="utf-8").read()
        if "(legal/)" not in s:
            open(cuh_index, "a", encoding="utf-8").write(
                "\n* [Legal — U.S. law](legal/) - statutes, regulations, court rules, case law, agency decisions\n")

def main():
    ap = argparse.ArgumentParser(description="Build the Cuh legal knowledge base")
    ap.add_argument("--root", default=os.path.expanduser("~/Documents/OpenKnowledge/Cuh/legal"))
    ap.add_argument("--cache", default=os.path.expanduser(
        "~/Library/Caches/cuh-legal" if platform.system() == "Darwin" else "~/.cache/cuh-legal"))
    ap.add_argument("--layers", default="all")
    ap.add_argument("--force", default="", help="comma list of layers to rebuild even if done")
    ap.add_argument("--fr-from", type=int, default=2000)
    ap.add_argument("--caselaw-scope", default="all",
                    help="all | comma list of jurisdictions (federal,california,new-york,...,tribal)")
    ap.add_argument("--cl-date", default="2026-09-30", help="CourtListener quarterly bulk snapshot date")
    ap.add_argument("--usc-release", default="119-120", help="OLRC release point, e.g. 119-120")
    ap.add_argument("--keep-cache", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-icloud", action="store_true", help="build even if Documents syncs to iCloud")
    a = ap.parse_args()
    root, cache = os.path.abspath(a.root), os.path.abspath(a.cache)
    want = ORDER if a.layers == "all" else [l.strip() for l in a.layers.split(",")]
    bad = [l for l in want if l not in LAYERS]
    if bad:
        raise SystemExit(f"unknown layer(s): {bad}; choose from {ORDER}")
    state_p = os.path.join(root, "references", "build-state.json")
    state = json.load(open(state_p)) if os.path.exists(state_p) else {"done": {}}
    force = {x.strip() for x in a.force.split(",") if x.strip()}
    todo = [l for l in ORDER + ["case-law-cap"] if l in want and (l not in state["done"] or l in force or l in ("fr",))]

    need = sum(EST_GB[l] for l in todo if l not in state["done"] or l in force)
    peak = max([DL_GB.get(l, 0) for l in todo] or [0])
    fg = free_gb(root)
    log(f"root:  {root}")
    log(f"cache: {cache}")
    log(f"layers to build: {', '.join(todo) or '(nothing — all done)'}")
    log(f"estimated output ~{need:.0f} GB + peak cache ~{peak:.1f} GB; free on disk: {fg:.0f} GB")
    icloud = icloud_synced(root)
    if icloud:
        log("WARNING: ~/Documents appears to sync to iCloud Drive. Millions of files / tens of GB would upload.")
    if a.dry_run:
        return
    if icloud and not a.allow_icloud and any(l in todo for l in ("fr", "bva", "case-law", "agency")):
        raise SystemExit("Refusing large layers on an iCloud-synced Documents folder. Re-run with --allow-icloud, "
                         "or --root on a non-synced path, or build the small layers only.")
    if need + peak > fg * 0.9:
        raise SystemExit(f"Not enough free disk ({fg:.0f} GB) for ~{need + peak:.0f} GB. Free space or choose fewer layers.")
    os.makedirs(os.path.join(root, "references"), exist_ok=True)
    os.makedirs(cache, exist_ok=True)
    for l in todo:
        log(f"=== layer: {l}")
        t0 = time.time()
        result = LAYERS[l](root, cache, a)
        state["done"][l] = {"at": dt.datetime.now().isoformat(timespec="seconds"),
                            "minutes": round((time.time() - t0) / 60, 1), "result": result or "ok"}
        json.dump(state, open(state_p, "w"), indent=1)
        write_root(root, state["done"])
        log(f"=== {l} finished in {state['done'][l]['minutes']} min")
    write_root(root, state["done"])
    log("all requested layers complete.")

if __name__ == "__main__":
    main()

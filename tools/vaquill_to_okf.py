#!/usr/bin/env python3
"""
vaquill_to_okf.py — State law from Vaquill's Open US Law snapshot -> OKF v0.2 concepts.

Source: https://oss-data-us.vaquill.ai/index.json (Cloudflare R2 mirror of the Hugging Face
dataset vaquill/open-us-law; per-file SHA256 in the manifest). Snapshot v2026.09.1.

LICENSING — read before commercial use:
  * statutory / regulatory text: public domain (government edicts doctrine)
  * Vaquill's compilation (structure, citations, assembly) in v2026.09+: CC BY-NC 4.0
    -> NON-COMMERCIAL. Every concept records this in `compilation_license`. For commercial
    use, license from Vaquill or use the official-source state layer (tools/states_own/).

Kinds converted (state / DC / PR files only; federal layers come from official sources):
  statutes, constitutions, regulations, court_rules, ag_opinion, guidance, administrative_guidance
California statutes + constitution are skipped (built from leginfo's official database).

Output: <out>/<state-name>/<kind>/<title>/<chapter>/sec-<n>.md  (+ indexes, report)
Requires: pyarrow.
"""
import argparse, datetime as dt, hashlib, json, os, re, sys, time, urllib.request
from collections import defaultdict, Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uslm_to_okf as U

TOOL = "vaquill_to_okf/1.0"
UA = "OKF-LawCorpus/1.0 (open legal-knowledge research; contact " + os.environ.get(
    "LEGAL_US_CONTACT", "see github.com/MetatronAngelAgent/legal-united-states") + ")"
INDEX = "https://oss-data-us.vaquill.ai/index.json"
STATES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california", "co": "colorado",
    "ct": "connecticut", "de": "delaware", "dc": "district-of-columbia", "fl": "florida", "ga": "georgia",
    "hi": "hawaii", "id": "idaho", "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas",
    "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland", "ma": "massachusetts",
    "mi": "michigan", "mn": "minnesota", "ms": "mississippi", "mo": "missouri", "mt": "montana",
    "ne": "nebraska", "nv": "nevada", "nh": "new-hampshire", "nj": "new-jersey", "nm": "new-mexico",
    "ny": "new-york", "nc": "north-carolina", "nd": "north-dakota", "oh": "ohio", "ok": "oklahoma",
    "or": "oregon", "pa": "pennsylvania", "pr": "puerto-rico", "ri": "rhode-island", "sc": "south-carolina",
    "sd": "south-dakota", "tn": "tennessee", "tx": "texas", "ut": "utah", "vt": "vermont", "va": "virginia",
    "wa": "washington", "wv": "west-virginia", "wi": "wisconsin", "wy": "wyoming",
}
KINDS = {  # file kind -> (folder, OKF type)
    "statutes": ("statutes", "Statute Section"),
    "constitutions": ("constitution", "Constitutional Provision"),
    "regulations": ("regulations", "Regulation Section"),
    "court_rules": ("court-rules", "Court Rule"),
    "ag_opinion": ("ag-opinions", "Attorney General Opinion"),
    "guidance": ("guidance", "Guidance Document"),
    "administrative_guidance": ("guidance", "Guidance Document"),
}
SKIP = {("ca", "statutes"), ("ca", "constitutions")}   # official-source layer exists

def nz(v):
    v = "" if v is None else str(v)
    return "" if v.strip() in ("None", "nan", "null", "[]") else v.strip()

def get(url, dest):
    for a in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=1800) as r, open(dest + ".part", "wb") as f:
                while True:
                    b = r.read(1 << 20)
                    if not b: break
                    f.write(b)
            os.replace(dest + ".part", dest)
            return
        except Exception as e:
            if a == 3: raise
            time.sleep(10 * (a + 1))

def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()

def jlist(s):
    s = nz(s)
    if not s: return []
    try:
        v = json.loads(s)
        return [str(x) for x in v] if isinstance(v, list) else []
    except Exception:
        return []

def hier_dirs(r):
    """Directory levels from the breadcrumb (every level except the section itself), e.g.
    code-ag/chapter-101. Falls back to title_number/chapter when there is no breadcrumb."""
    out = []
    try:
        bc = json.loads(nz(r.get("breadcrumb")) or "[]")
    except Exception:
        bc = []
    if bc and isinstance(bc[0], dict):
        for lv in bc[:-1]:
            typ, num = U.slug(str(lv.get("type") or "level"))[:20], str(lv.get("num") or lv.get("label") or "")
            if num and num not in ("None",):
                out.append(f"{typ}-{U.slug(num)[:50]}")
    elif bc and isinstance(bc[0], str):          # plain list of labels
        out = [U.slug(x)[:60] for x in bc[1:-1] if x]
    if not out:
        out = [f"{n}-{U.slug(v)[:50]}" for n, v in (("title", nz(r.get("title_number"))), ("chapter", nz(r.get("chapter")))) if v]
    return out

def clean_heading(title, secnum):
    t = re.sub(r"^\s*(?:§+|Sec\.|Section|Rule|r\.)?\s*" + re.escape(secnum) + r"\.?\s*", "", title or "", flags=re.I).strip()
    return t or title

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", default="legal")
    ap.add_argument("--states", default="", help="comma list of 2-letter codes (default all)")
    ap.add_argument("--kinds", default=",".join(KINDS))
    ap.add_argument("--split-chars", type=int, default=6000)
    ap.add_argument("--keep-cache", action="store_true")
    a = ap.parse_args()
    import pyarrow.parquet as pq
    os.makedirs(a.cache, exist_ok=True)
    idx_p = os.path.join(a.cache, "index.json")
    get(INDEX, idx_p)
    man = json.load(open(idx_p))
    snap, ver = man.get("snapshot_date", ""), man.get("version", "")
    want_states = {s.strip() for s in a.states.split(",") if s.strip()}
    want_kinds = {k.strip() for k in a.kinds.split(",") if k.strip()}
    run_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stats_p = os.path.join(a.out, "references", "ingest-stats-states-vaquill.json")
    os.makedirs(os.path.dirname(stats_p), exist_ok=True)
    stats = json.load(open(stats_p)) if os.path.exists(stats_p) else {}
    touched = set()

    for f in man["files"]:
        m = re.match(r"us_([a-z]{2})_(.+)\.parquet$", f["file"])
        if not m: continue
        st, kind = m.groups()
        if st not in STATES or kind not in KINDS or kind not in want_kinds: continue
        if want_states and st not in want_states: continue
        if (st, kind) in SKIP: continue
        key = f"{st}:{kind}"
        if stats.get(key, {}).get("sha256") == f["sha256"]:
            continue                                   # already converted this exact file
        local = os.path.join(a.cache, f["file"])
        if not (os.path.exists(local) and sha256(local) == f["sha256"]):
            get(f["url"], local)
            if sha256(local) != f["sha256"]:
                print(f"  CHECKSUM MISMATCH {f['file']} — skipped", flush=True)
                stats[key] = {"error": "checksum mismatch"}; continue
        folder, otype = KINDS[kind]
        sname = STATES[st]
        base = os.path.join(a.out, sname, folder)
        rows = written = empty = no_src = deprecated = 0
        seen = set()
        pf = pq.ParquetFile(local)
        for batch in pf.iter_batches(batch_size=2000):
            for r in batch.to_pylist():
                rows += 1
                text = nz(r.get("text"))
                cite = nz(r.get("citation")) or nz(r.get("citation_short")) or nz(r.get("act_id"))
                if not text:
                    empty += 1
                ddir = os.path.join(base, *hier_dirs(r))
                secnum = nz(r.get("section_number")) or nz(r.get("act_id")) or hashlib.sha1(cite.encode()).hexdigest()[:10]
                fname = f"sec-{U.slug(secnum)[:80]}"
                path = os.path.join(ddir, fname + ".md")
                k = 2
                while path in seen:
                    path = os.path.join(ddir, f"{fname}--v{k}.md"); k += 1
                seen.add(path)
                status_raw = nz(r.get("act_status")) or "in_force"
                status = "stable" if status_raw in ("in_force", "active", "current") else "deprecated"
                deprecated += status == "deprecated"
                src = nz(r.get("source_url"))
                if not src:
                    no_src += 1
                title = clean_heading(nz(r.get("section_title")) or nz(r.get("display_path")), nz(r.get("section_number")))
                full_title = f"{cite} — {title}" if title and title not in cite else (cite or title)
                links = []
                for c in jlist(r.get("cross_references_usc")):
                    mm = re.match(r"^\s*(\d+)\s*U\.?S\.?C\.?\s*§*\s*([\w.\-]+)", c)
                    links.append(f"- {c}" if not mm else f"- [{c}](/federal/usc/title-{mm.group(1)}/sec-{U.slug(mm.group(2))}.md)")
                for c in jlist(r.get("cross_references_cfr")):
                    mm = re.match(r"^\s*(\d+)\s*C\.?F\.?R\.?\s*(?:§|part)?\s*(\d+)", c, re.I)
                    links.append(f"- {c}" if not mm else f"- [{c}](/federal/cfr/title-{mm.group(1)}/part-{mm.group(2)}/index.md)")
                body = text or "(No text in source snapshot.)"
                pairs = [
                    ("type", otype), ("title", full_title[:300]),
                    ("description", (title or text[:200] or cite)[:240]),
                    ("resource", src or f"https://huggingface.co/datasets/vaquill/open-us-law ({f['file']}, {nz(r.get('act_id'))})"),
                    ("tags", [sname, folder, "state-law"]),
                    ("citation", cite), ("jurisdiction", sname), ("jurisdiction_level", "state"),
                    ("hierarchy", nz(r.get("display_path"))),
                    ("last_amended", nz(r.get("last_amended_year"))),
                    ("act_status", status_raw), ("status", status),
                    ("official_status", nz(r.get("official_status"))), ("currency_note", nz(r.get("currency_note"))),
                    ("source_note", "" if src else "No official source URL — Vaquill marks these records as aggregator-sourced; verify against the official code"),
                    ("license", "Statutory/regulatory text: public domain"),
                    ("compilation_license", f"CC BY-NC 4.0 (Vaquill Open US Law {ver}) — non-commercial"),
                    ("dataset_snapshot", snap),
                    ("generated", {"by": TOOL, "at": run_at}),
                    ("sources", ("raw", "\n  - { id: vaquill, resource: " + U.yq(f["url"]) +
                                 ", title: " + U.yq(f"Open US Law {ver} — {f['file']}") +
                                 ", author: " + U.yq("process:vaquill-open-us-law") +
                                 ", last_modified: " + U.yq(snap + "T00:00:00Z") + " }" +
                                 ("\n  - { id: official, resource: " + U.yq(src) + ", title: " + U.yq("Official source") + " }" if src else ""))),
                    ("checksum", hashlib.sha256(body.encode()).hexdigest()),
                ]
                xref = ("# Cross-references\n\n" + "\n".join(links) + "\n\n") if links else ""
                os.makedirs(ddir, exist_ok=True)
                paras = [p for p in re.split(r"\n\s*\n|\n(?=\(\w{1,4}\)\s)", body) if p.strip()]
                if len(body) <= a.split_chars or len(paras) < 2:
                    open(path, "w", encoding="utf-8").write(U.emit_frontmatter(pairs) + "\n" + xref + "# Text\n\n" + body + "\n")
                else:
                    chunks, cur, sz = [], [], 0
                    for p in paras:
                        if cur and sz + len(p) > a.split_chars:
                            chunks.append(cur); cur, sz = [], 0
                        cur.append(p); sz += len(p) + 2
                    if cur: chunks.append(cur)
                    pdir = path[:-3]; os.makedirs(pdir, exist_ok=True)
                    lk = []
                    for i, c in enumerate(chunks, 1):
                        pb = "\n\n".join(c); pt = f"{full_title[:250]} (part {i} of {len(chunks)})"
                        pp = [(x, (pt if x == "title" else hashlib.sha256(pb.encode()).hexdigest() if x == "checksum" else v))
                              for x, v in pairs] + [("part_of", f"{i} of {len(chunks)}")]
                        open(os.path.join(pdir, f"part-{i:03d}.md"), "w", encoding="utf-8").write(
                            U.emit_frontmatter(pp) + "\n# Text\n\n" + pb + "\n")
                        lk.append((f"{os.path.basename(pdir)}/part-{i:03d}.md", pt))
                    open(path, "w", encoding="utf-8").write(
                        U.emit_frontmatter(pairs + [("parts", str(len(chunks)))]) + "\n" + xref +
                        "# Text\n\n" + paras[0] + "\n\n# Parts\n\n" + "\n".join(f"- [{t}]({p})" for p, t in lk) + "\n")
                written += 1
        stats[key] = {"rows": rows, "written": written, "empty_text": empty, "no_official_url": no_src,
                      "not_in_force": deprecated, "sha256": f["sha256"], "version": ver}
        json.dump(stats, open(stats_p, "w"), indent=1)
        touched.add(base)
        if not a.keep_cache:
            os.remove(local)
        print(f"{st} {kind}: {written}/{rows} written, {no_src} without official URL, {empty} empty", flush=True)

    # indexes for every directory under the touched kind folders
    for base in sorted(touched):
        for dp, dns, fns in os.walk(base, topdown=False):
            if os.path.basename(dp).startswith("sec-"):   # split-part folders have their own content
                continue
            items = []
            for fn in sorted(fns):
                if fn.endswith(".md") and fn != "index.md":
                    t = re.search(r'^title: "(.*)"$', open(os.path.join(dp, fn), encoding="utf-8").read(3000), re.M)
                    items.append(f"* [{t.group(1) if t else fn}]({fn})")
            subs = [f"* [{d}]({d}/)" for d in sorted(dns) if not d.startswith("sec-")]
            head = os.path.relpath(dp, a.out).replace(os.sep, " / ").replace("-", " ").title()
            open(os.path.join(dp, "index.md"), "w", encoding="utf-8").write(
                f"# {head}\n\n" + "\n".join(subs + ([""] if subs and items else []) + items) + "\n")
        sdir = os.path.dirname(base)
        kinds_here = sorted(d for d in os.listdir(sdir) if os.path.isdir(os.path.join(sdir, d)))
        open(os.path.join(sdir, "index.md"), "w", encoding="utf-8").write(
            f"# {os.path.basename(sdir).replace('-', ' ').title()}\n\n" +
            "\n".join(f"* [{k.replace('-', ' ').title()}]({k}/)" for k in kinds_here) + "\n")
    print("done", flush=True)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
cap_to_okf.py — U.S. case law from the Caselaw Access Project (Common Pile release,
huggingface.co/datasets/common-pile/caselaw_access_project, public domain) -> OKF v0.2.

Streams the 173 gzipped JSONL shards one at a time (download -> convert -> delete), so disk
use stays ~1 shard. Each opinion becomes one concept:
  <out>/case-law/<jurisdiction>/<reporter>/vol-<V>/<page>[-<n>].md
with the official reporter citation derived from the record id (e.g. us_528/html/0935-14 ->
"528 U.S. 935"), case name, court, decision date, docket, and a "# Cited Authorities" section
that links every U.S.C. / C.F.R. / California code citation in the opinion to that section's
concept in the bundle (resolved against what exists under <out>/).

Default scope: California + federal reporters (--scope ca,federal). --scope all keeps every
jurisdiction (state = reporter prefix). Stdlib only.
"""
import argparse, datetime as dt, gzip, hashlib, json, os, re, sys, time, urllib.request
from collections import defaultdict, Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uslm_to_okf as U

TOOL = "cap_to_okf/1.0"
UA = "OKF-LawCorpus/1.0 (open legal-knowledge research; contact " + os.environ.get("LEGAL_US_CONTACT", "see github.com/MetatronAngelAgent/legal-united-states") + ")"
HF = "https://huggingface.co/datasets/common-pile/caselaw_access_project/resolve/main"
N_SHARDS = 173

FEDERAL = {
    "us": "U.S.", "s-ct": "S. Ct.", "l-ed": "L. Ed.", "l-ed-2d": "L. Ed. 2d",
    "f": "F.", "f2d": "F.2d", "f3d": "F.3d", "f-appx": "F. App'x", "f-cas": "F. Cas.",
    "f-supp": "F. Supp.", "f-supp-2d": "F. Supp. 2d", "f-supp-3d": "F. Supp. 3d",
    "f-r-d": "F.R.D.", "b-r": "B.R.", "fed-cl": "Fed. Cl.", "ct-cl": "Ct. Cl.", "cl-ct": "Cl. Ct.",
    "ct-intl-trade": "Ct. Int'l Trade", "cust-ct": "Cust. Ct.", "ccpa": "C.C.P.A.",
    "vet-app": "Vet. App.", "m-j": "M.J.", "mj": "M.J.", "cma": "C.M.A.", "cmr": "C.M.R.",
    "t-c": "T.C.", "bta": "B.T.A.", "fed-reg": "Fed. Reg.", "app-dc": "App. D.C.",
    "us-app-dc": "U.S. App. D.C.", "ct-cust-app": "Ct. Cust. App.", "afcca": "A.F.C.C.A.",
}
def reporter_abbr(slug):
    if slug in FEDERAL:
        return FEDERAL[slug]
    # generic: cal-app-4th -> Cal. App. 4th ; cal-rptr-3d -> Cal. Rptr. 3d
    parts = slug.split("-")
    out = []
    for p in parts:
        if re.fullmatch(r"\d+(d|th|st|nd|rd)", p):
            out.append(p)
        elif p == "app":
            out.append("App.")
        else:
            out.append(p.capitalize() + ".")
    return " ".join(out)

def jurisdiction_of(slug):
    if slug in FEDERAL:
        return "federal"
    if slug == "cal" or slug.startswith("cal-"):
        return "california"
    return re.sub(r"-.*$", "", slug)          # other states: reporter prefix (e.g. kan, iowa)

# ---------- citation extraction & resolution against the bundle ----------
CA_CODES = [
    (r"Code\s+(?:of\s+)?Civ(?:il|\.)\s+Proc(?:edure|\.)", "CCP"), (r"Pen(?:al|\.)\s+Code", "PEN"),
    (r"Civ(?:il|\.)\s+Code", "CIV"), (r"Evid(?:ence|\.)\s+Code", "EVID"), (r"Gov(?:ernment|\.)\s+Code", "GOV"),
    (r"Lab(?:or|\.)\s+Code", "LAB"), (r"Veh(?:icle|\.)\s+Code", "VEH"), (r"Fam(?:ily|\.)\s+Code", "FAM"),
    (r"Prob(?:ate|\.)\s+Code", "PROB"), (r"Bus(?:iness|\.)\s+(?:&|and)\s+Prof(?:essions|\.)\s+Code", "BPC"),
    (r"Health\s+(?:&|and)\s+Saf(?:ety|\.)\s+Code", "HSC"), (r"Welf(?:are|\.)\s+(?:&|and)\s+Inst(?:itutions|\.)\s+Code", "WIC"),
    (r"Ins(?:urance|\.)\s+Code", "INS"), (r"Corp(?:orations|\.)\s+Code", "CORP"),
    (r"Rev(?:enue|\.)\s+(?:&|and)\s+Tax(?:ation|\.)\s+Code", "RTC"), (r"Pub(?:lic|\.)\s+Res(?:ources|\.)\s+Code", "PRC"),
    (r"Pub(?:lic|\.)\s+Util(?:ities|\.)\s+Code", "PUC"), (r"Com(?:mercial|\.)\s+Code", "COM"),
    (r"Educ(?:ation|\.)\s+Code", "EDC"), (r"Elec(?:tions|\.)\s+Code", "ELEC"), (r"Fin(?:ancial|\.)\s+Code", "FIN"),
    (r"Wat(?:er|\.)\s+Code", "WAT"), (r"Unemp(?:loyment|\.)\s+Ins(?:urance|\.)\s+Code", "UIC"),
]
CA_RE = [(re.compile(r"(?:" + pat + r")\s*,?\s*(?:§§?|sections?|secs?\.)\s*([\d]+(?:\.\d+)*)", re.I), code)
         for pat, code in CA_CODES] + \
        [(re.compile(r"(?:§§?|sections?)\s*([\d]+(?:\.\d+)*)\s+of\s+the\s+(?:" + pat + r")", re.I), code)
         for pat, code in CA_CODES]
USC_RE = re.compile(r"\b(\d{1,2})\s*U\.\s?S\.\s?C\.?\s*(?:A\.\s*)?(?:§§?\s*)?(\d+[a-z]?(?:-\d+)?)")
CFR_RE = re.compile(r"\b(\d{1,2})\s*C\.\s?F\.\s?R\.?\s*(?:§§?\s*)?(\d+)\.(\d+[a-z]?)")

class Resolver:
    """Maps (kind, a, b) -> bundle-relative concept path, from files that exist under out/."""
    def __init__(self, out):
        self.usc, self.ca, self.cfr = {}, {}, {}
        for base, store, rx in (
            (os.path.join(out, "federal", "usc"), self.usc, re.compile(r"title-([\w]+)/.*?sec-([\w\-]+)\.md$")),
            (os.path.join(out, "california", "codes"), self.ca, re.compile(r"codes/([a-z]+)/.*?sec-([\w\-]+)\.md$")),
            (os.path.join(out, "federal", "cfr"), self.cfr, re.compile(r"title-(\d+)/part-[\w\-]+/sec-([\w\-]+)\.md$")),
        ):
            if not os.path.isdir(base):
                continue
            for root, _, files in os.walk(base):
                for f in files:
                    if not f.startswith("sec-") or "--" in f:
                        continue
                    p = os.path.join(root, f)
                    m = rx.search(p.replace(os.sep, "/"))
                    if m:
                        store.setdefault((m.group(1).upper(), m.group(2)), "/" + os.path.relpath(p, out).replace(os.sep, "/"))
        print(f"resolver: {len(self.usc)} USC, {len(self.ca)} CA code, {len(self.cfr)} CFR sections indexed", flush=True)

    def links(self, text):
        seen, out = set(), []
        for m in USC_RE.finditer(text):
            key = ("usc", m.group(1), m.group(2))
            if key in seen: continue
            seen.add(key)
            p = self.usc.get((m.group(1).upper(), U.slug(m.group(2))))
            out.append((f"{m.group(1)} U.S.C. § {m.group(2)}", p))
        for m in CFR_RE.finditer(text):
            sec = f"{m.group(2)}.{m.group(3)}"
            key = ("cfr", m.group(1), sec)
            if key in seen: continue
            seen.add(key)
            p = self.cfr.get((m.group(1), U.slug(sec)))
            out.append((f"{m.group(1)} C.F.R. § {sec}", p))
        for rx, code in CA_RE:
            for m in rx.finditer(text):
                key = ("ca", code, m.group(1))
                if key in seen: continue
                seen.add(key)
                p = self.ca.get((code, U.slug(m.group(1))))
                out.append((f"Cal. {code} § {m.group(1)}", p))
        return out[:200]

# ---------- header parsing ----------
DATE_RE = re.compile(r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
                     r"Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{1,2}),?\s+(\d{4})")
MONTHS = {m: i for i, m in enumerate(["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"], 1)}

def parse_header(text):
    parts = [p.strip() for p in re.split(r"\s{3,}|\n+", text.strip()[:3000]) if p.strip()]
    name = parts[0] if parts else ""
    docket = next((p for p in parts[1:8] if re.match(r"^(No|Nos|Docket|Civ|Crim|Case)\b\.?", p, re.I)), "")
    court = next((p for p in parts[1:10] if re.search(r"\b(Court|Supreme|Appeals|Circuit|District|Tribunal|Board)\b", p)
                  and len(p) < 160 and not re.match(r"^(APPEAL|Appeal|PETITION|Petition|ORIGINAL|Certiorari|ON|On)\b", p)), "")
    date = None
    for p in parts[1:12]:
        m = DATE_RE.search(p)
        if m:
            try:
                date = dt.date(int(m.group(3)), MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
                break
            except ValueError:
                pass
    return name[:300], docket[:200], court, date

def fetch_shard(i, cache):
    fn = os.path.join(cache, f"cap_{i:05d}.jsonl.gz")
    if os.path.exists(fn) and os.path.getsize(fn) > 0:
        return fn
    req = urllib.request.Request(f"{HF}/cap_{i:05d}.jsonl.gz", headers={"User-Agent": UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=1800) as r, open(fn + ".part", "wb") as out:
                while True:
                    b = r.read(1 << 20)
                    if not b: break
                    out.write(b)
            os.replace(fn + ".part", fn)
            return fn
        except Exception as e:
            if attempt == 3: raise
            time.sleep(15 * (attempt + 1))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", default="legal")
    ap.add_argument("--scope", default="ca,federal", help="ca,federal | all")
    ap.add_argument("--shards", default="", help="e.g. 0-172 or 44,172 (default all)")
    ap.add_argument("--keep-shards", action="store_true")
    ap.add_argument("--split-chars", type=int, default=6000)
    a = ap.parse_args()
    os.makedirs(a.cache, exist_ok=True)
    scope = {s.strip() for s in a.scope.split(",")}
    keep = (lambda j: True) if "all" in scope else (lambda j: (j == "federal" and "federal" in scope) or
                                                          (j == "california" and "ca" in scope))
    if a.shards:
        ids = []
        for part in a.shards.split(","):
            if "-" in part:
                x, y = part.split("-"); ids += list(range(int(x), int(y) + 1))
            else:
                ids.append(int(part))
    else:
        ids = list(range(N_SHARDS))
    run_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    res = Resolver(a.out)
    root = os.path.join(a.out, "case-law")
    statep = os.path.join(a.out, "references", "ingest-state-caselaw.json")
    os.makedirs(os.path.dirname(statep), exist_ok=True)
    state = json.load(open(statep)) if os.path.exists(statep) else {"done_shards": [], "stats": {}}
    stats = Counter(state["stats"]); unknown = Counter()
    for i in ids:
        if i in state["done_shards"]:
            continue
        fn = fetch_shard(i, a.cache)
        n_kept = 0
        with gzip.open(fn, "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    stats["bad_json"] += 1; continue
                rid = d.get("id", "")
                m = re.match(r"^([a-z0-9\-]+)_(\d+)/html/(.+)-(\d+)\.html$", rid)   # page may be "XIII", "Supp. 757"
                if not m:
                    stats["non_cap_id"] += 1; unknown[rid.split("/")[0][:30]] += 1; continue
                slug, vol, page, seq = m.group(1), m.group(2), m.group(3), m.group(4)
                jur = jurisdiction_of(slug)
                if not keep(jur):
                    stats["out_of_scope"] += 1; continue
                text = d.get("text") or ""
                if len(text.strip()) < 100:      # one-line orders, e.g. "Certiorari denied."
                    stats["one_line_orders_skipped"] += 1; continue
                name, docket, court, date = parse_header(text)
                abbr = reporter_abbr(slug)
                pg = str(int(page)) if page.isdigit() else page
                cite = f"{vol} {abbr} {pg}"
                title = f"{name} — {cite}" + (f" ({date.year})" if date else "")
                body_raw = re.sub(r"[ \t]{3,}", "\n\n", text.strip())
                paras = [U.normspace(p) for p in re.split(r"\n\s*\n", body_raw) if U.normspace(p)]
                body = "\n\n".join(paras)
                cites = res.links(text)
                auth = ""
                if cites:
                    auth = "# Cited Authorities\n\n" + "\n".join(
                        (f"- [{c}]({p})" if p else f"- {c}") for c, p in cites) + "\n\n"
                    stats["citations"] += len(cites); stats["citations_linked"] += sum(1 for _, p in cites if p)
                author = (d.get("metadata") or {}).get("author") or ""
                pairs = [
                    ("type", "Judicial Opinion"), ("title", title[:300]),
                    ("description", (f"{court}, {date.isoformat()}" if court and date else court or cite)[:240]),
                    ("resource", f"https://static.case.law/{slug}/{vol}/cases/{page}-{seq}.json"),
                    ("tags", ["case-law", jur, slug]),
                    ("citation", cite), ("case_name", name), ("court", court),
                    ("decision_date", date.isoformat() if date else ""), ("docket", docket),
                    ("reporter", abbr), ("opinion_author", U.normspace(author)[:120]),
                    ("jurisdiction", jur),
                    ("jurisdiction_level", "federal" if jur == "federal" else "state"),
                    ("license", "Public domain (Caselaw Access Project / Common Pile)"),
                    ("status", "stable"),
                    ("generated", {"by": TOOL, "at": run_at}),
                    ("sources", ("raw", "\n  - { id: cap, resource: " + U.yq(f"{HF.replace('/resolve/main','')} (cap_{i:05d}, {rid})") +
                                 ", title: " + U.yq("Caselaw Access Project — Common Pile release") +
                                 ", author: " + U.yq("process:harvard-lil-cap") + " }")),
                    ("checksum", hashlib.sha256(body.encode()).hexdigest()),
                ]
                ddir = os.path.join(root, jur, slug, f"vol-{vol}")
                os.makedirs(ddir, exist_ok=True)
                path = os.path.join(ddir, f"{U.slug(pg)}-{int(seq)}.md")
                if len(body) <= a.split_chars or len(paras) < 2:
                    open(path, "w", encoding="utf-8").write(U.emit_frontmatter(pairs) + "\n" + auth + "# Opinion\n\n" + body + "\n")
                else:
                    chunks, cur, sz = [], [], 0
                    for p in paras:
                        if cur and sz + len(p) > a.split_chars:
                            chunks.append(cur); cur, sz = [], 0
                        cur.append(p); sz += len(p) + 2
                    if cur: chunks.append(cur)
                    pdir = path[:-3]; os.makedirs(pdir, exist_ok=True)
                    lk = []
                    for k, c in enumerate(chunks, 1):
                        pb = "\n\n".join(c); pt = f"{title[:250]} (part {k} of {len(chunks)})"
                        pp = [(x, (pt if x == "title" else hashlib.sha256(pb.encode()).hexdigest() if x == "checksum" else v))
                              for x, v in pairs] + [("part_of", f"{k} of {len(chunks)}")]
                        open(os.path.join(pdir, f"part-{k:03d}.md"), "w", encoding="utf-8").write(
                            U.emit_frontmatter(pp) + "\n# Opinion\n\n" + pb + "\n")
                        lk.append((f"{os.path.basename(pdir)}/part-{k:03d}.md", pt))
                    open(path, "w", encoding="utf-8").write(
                        U.emit_frontmatter(pairs + [("parts", str(len(chunks)))]) + "\n" + auth +
                        "# Opinion\n\n" + paras[0] + "\n\n# Parts\n\n" + "\n".join(f"- [{t}]({p})" for p, t in lk) + "\n")
                    stats["split"] += 1
                stats["opinions"] += 1; stats[f"jur:{jur}"] += 1; n_kept += 1
        state["done_shards"].append(i); state["stats"] = dict(stats)
        json.dump(state, open(statep, "w"), indent=1)
        if not a.keep_shards:
            os.remove(fn)
        print(f"shard {i:03d}: kept {n_kept} | total opinions {stats['opinions']}", flush=True)

    # indexes: case-law / jurisdiction / reporter / volume
    for jur in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        jdir = os.path.join(root, jur)
        reps = sorted(os.listdir(jdir))
        for rep in reps:
            rdir = os.path.join(jdir, rep)
            if not os.path.isdir(rdir): continue
            vols = sorted((v for v in os.listdir(rdir) if v.startswith("vol-")), key=lambda v: int(v[4:]))
            for v in vols:
                vdir = os.path.join(rdir, v)
                items = []
                for f in sorted(os.listdir(vdir), key=lambda s: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s)]):
                    if f.endswith(".md") and f != "index.md":
                        t = re.search(r'^title: "(.*)"$', open(os.path.join(vdir, f), encoding="utf-8").read(2000), re.M)
                        items.append(f"* [{t.group(1) if t else f}]({f})")
                open(os.path.join(vdir, "index.md"), "w", encoding="utf-8").write(
                    f"# {reporter_abbr(rep)} — volume {v[4:]}\n\n" + "\n".join(items) + "\n")
            open(os.path.join(rdir, "index.md"), "w", encoding="utf-8").write(
                f"# {reporter_abbr(rep)}\n\n" + "\n".join(f"* [Volume {v[4:]}]({v}/)" for v in vols) + "\n")
        open(os.path.join(jdir, "index.md"), "w", encoding="utf-8").write(
            f"# Case law — {jur.title()}\n\n" + "\n".join(f"* [{reporter_abbr(r)}]({r}/)" for r in reps if os.path.isdir(os.path.join(jdir, r))) + "\n")
    if os.path.isdir(root):
        open(os.path.join(root, "index.md"), "w", encoding="utf-8").write(
            "# Case Law\n\nSource: Caselaw Access Project (Harvard Law School Library), Common Pile release. Public domain.\n\n" +
            "\n".join(f"* [{j.title()}]({j}/)" for j in sorted(os.listdir(root)) if os.path.isdir(os.path.join(root, j))) + "\n")
    print("done:", dict(stats), "| unknown id prefixes:", unknown.most_common(5), flush=True)

if __name__ == "__main__":
    main()

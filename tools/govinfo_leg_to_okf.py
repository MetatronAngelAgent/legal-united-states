#!/usr/bin/env python3
"""
govinfo_leg_to_okf.py — Legislative branch: Congress's bills, bill status and enacted public laws
(GPO govinfo bulk data, public domain) -> OKF v0.2 concepts.

Collections
  plaw        Public and private laws as enacted (USLM XML; 113th Congress-present)
                -> federal/legislative/public-laws/<congress>/pl-<congress>-<n>.md  (private: pvtl-…)
  billstatus  Bill status from Congress.gov/CRS: titles, sponsor, cosponsor count, committees,
              every action, latest CRS summary, related bills, resulting law (108th-present)
                -> federal/legislative/bills/<congress>/<type>/<type>-<n>/status.md
  bills       Every printed version of every bill and resolution (introduced, reported, engrossed,
              enrolled, …) (113th-present)
                -> federal/legislative/bills/<congress>/<type>/<type>-<n>/text-<version>.md

Incremental: govinfo's XML directory listings carry a last-modified time and size per file.
The listing seen on each run is kept in <out>/references/state/govinfo-<collection>.json; the
next run fetches only files that are new or changed. A directory never seen before is loaded
from its bulk zip in one request.

Usage
  python3 govinfo_leg_to_okf.py --out legal --congress 119                      # all three, current Congress
  python3 govinfo_leg_to_okf.py --out legal --collections plaw --congress 113-119
  python3 govinfo_leg_to_okf.py --out legal --congress 119 --report changes.jsonl  # daily job
Stdlib only.
"""
import argparse, datetime as dt, hashlib, html, io, json, os, re, sys, time, urllib.request, urllib.error, zipfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uslm_to_okf as U

TOOL = "govinfo_leg_to_okf/1.0"
UA = "OKF-LawCorpus/1.0 (open legal-knowledge research; contact " + os.environ.get(
    "LEGAL_US_CONTACT", "see github.com/MetatronAngelAgent/legal-united-states") + ")"
BULK = "https://www.govinfo.gov/bulkdata"
TYPES = ["hr", "s", "hjres", "sjres", "hconres", "sconres", "hres", "sres"]
TYPE_LABEL = {"hr": "H.R.", "s": "S.", "hjres": "H.J.Res.", "sjres": "S.J.Res.", "hconres": "H.Con.Res.",
              "sconres": "S.Con.Res.", "hres": "H.Res.", "sres": "S.Res."}
VERSIONS = {"ih": "Introduced in House", "is": "Introduced in Senate", "rh": "Reported in House",
            "rs": "Reported in Senate", "rfs": "Referred in Senate", "rfh": "Referred in House",
            "eh": "Engrossed in House", "es": "Engrossed in Senate", "enr": "Enrolled Bill",
            "pcs": "Placed on Calendar Senate", "pch": "Placed on Calendar House", "rds": "Received in Senate",
            "rdh": "Received in House", "ats": "Agreed to Senate", "ath": "Agreed to House",
            "eas": "Engrossed Amendment Senate", "eah": "Engrossed Amendment House", "cph": "Considered and Passed House",
            "cps": "Considered and Passed Senate", "pp": "Public Print", "rcs": "Reference Change Senate",
            "rch": "Reference Change House", "sc": "Sponsor Change", "ris": "Referral Instructions Senate",
            "rih": "Referral Instructions House", "fph": "Failed Passage House", "fps": "Failed Passage Senate",
            "lth": "Laid on Table in House", "lts": "Laid on Table in Senate", "hds": "Held at Desk Senate",
            "hdh": "Held at Desk House", "iph": "Indefinitely Postponed in House", "ips": "Indefinitely Postponed in Senate",
            "pap": "Printed as Passed", "rah": "Referred w/Amendments House", "ras": "Referred w/Amendments Senate",
            "re": "Reprint of an Amendment", "reah": "Re-engrossed Amendment House", "res": "Re-engrossed Amendment Senate",
            "renr": "Re-enrolled Bill", "rft": "Re-referred to Committee", "rtr": "Reported to House", "rts": "Reported to Senate"}

# ----------------------------------------------------------------------------- fetching
def get(url, tries=5, accept=None):
    for a in range(tries):
        try:
            h = {"User-Agent": UA}
            if accept: h["Accept"] = accept
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=900) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (404, 410) or a == tries - 1:
                raise
            time.sleep(min(180, 10 * (a + 1) ** 2))
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if a == tries - 1:
                raise
            time.sleep(10 * (a + 1))

def listing(path):
    """govinfo XML directory listing -> {name: (is_folder, 'mtime|size', link)}"""
    r = ET.fromstring(get(f"{BULK}/xml/{path}", accept="application/xml"))
    out = {}
    for f in r.iter("file"):
        out[f.findtext("name")] = (f.findtext("folder") == "true",
                                   f"{f.findtext('formattedLastModifiedTime')}|{f.findtext('size')}",
                                   f.findtext("link"))
    return out

def leaf_dirs(collection, congress):
    """Directories whose files we convert, e.g. BILLS/119/2/hr, BILLSTATUS/119/hr, PLAW/119/public."""
    if collection == "PLAW":
        return [f"PLAW/{congress}/{k}" for k in ("public", "private")]
    if collection == "BILLSTATUS":
        return [f"BILLSTATUS/{congress}/{t}" for t in TYPES]
    sessions = sorted(n for n, v in listing(f"BILLS/{congress}").items() if v[0] and n.isdigit())
    return [f"BILLS/{congress}/{s}/{t}" for s in sessions for t in TYPES]

def changed_files(collection, d, state, stats):
    """Yield (name, bytes) for files in directory d that are new/changed since the saved listing."""
    try:
        cur = listing(d)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return
        raise
    files = {n: v for n, v in cur.items() if not v[0] and n.endswith(".xml")}
    zips = [n for n, v in cur.items() if not v[0] and n.endswith(".zip")]
    seen = state.get(d)
    if seen is None and zips:
        # first sight of this directory: one bulk zip instead of thousands of requests
        data = get(f"{BULK}/{d}/{zips[0]}")
        stats["zips_downloaded"] += 1
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                b = os.path.basename(n)
                if b.endswith(".xml") and b in files:
                    yield b, z.read(n)
        state[d] = {n: v[1] for n, v in files.items()}
        return
    seen = seen or {}
    todo = [n for n, v in files.items() if seen.get(n) != v[1]]
    for n in sorted(todo):
        try:
            yield n, get(files[n][2])
            seen[n] = files[n][1]
        except Exception as e:
            stats["fetch_errors"] += 1
            print(f"  fetch error {n}: {e}", flush=True)
        time.sleep(0.2)
    state[d] = seen

# ----------------------------------------------------------------------------- XML -> markdown
def local(t):
    return t.rsplit("}", 1)[-1] if isinstance(t, str) else ""

STRUCT = {"division", "subdivision", "title", "subtitle", "part", "subpart", "chapter", "subchapter",
          "section", "subsection", "paragraph", "subparagraph", "clause", "subclause", "item", "subitem",
          "level"}
HEADS = {"enum", "num", "header", "heading"}
TEXTS = {"text", "content", "chapeau", "continuation", "p", "official-title", "officialTitle",
         "enacting-clause", "enactingFormula", "resolvingClause", "legis-type", "docTitle",
         "after-quoted-block", "proviso", "attestation-group", "attestation", "signatures"}
QUOTES = {"quoted-block", "quotedContent", "quotedText"}
SKIP = {"metadata", "meta", "toc", "sidenote", "page", "pagebreak", "processedBy", "dublinCore",
        "distribution-code", "endorsement", "footnote", "note", "notes", "preface", "coverTitle", "coverText"}

def inline(e):
    """Text of e without nested quoted blocks / structural children."""
    parts = [e.text or ""]
    for c in e:
        t = local(c.tag)
        if t not in QUOTES and t not in STRUCT and t not in SKIP:
            parts.append(inline(c))
        parts.append(c.tail or "")
    return re.sub(r"\s+", " ", "".join(parts)).strip()

def render(el, out, q=0):
    t = local(el.tag)
    pre = "> " * q
    if t in SKIP:
        return
    if t in QUOTES:
        for c in el:
            render(c, out, q + 1)
        return
    if t in STRUCT:
        head, used = [], set()
        for c in el:
            ct = local(c.tag)
            if ct in HEADS:
                s = inline(c)
                if s: head.append(f"**{s}**" if ct in ("header", "heading") else s)
                used.add(id(c))
            elif ct in TEXTS:
                s = inline(c)
                if s: head.append(s)
                used.add(id(c))
                for g in c:                                  # quoted blocks inside the lead text
                    if local(g.tag) in QUOTES or local(g.tag) in STRUCT:
                        if head:
                            out.append(pre + " ".join(head)); head = []
                        render(g, out, q)
                break
            else:
                break
        if head:
            out.append(pre + " ".join(head))
        for c in el:
            if id(c) not in used:
                render(c, out, q)
        return
    if t in TEXTS:
        s = inline(el)
        if s: out.append(pre + s)
        for g in el:
            if local(g.tag) in QUOTES or local(g.tag) in STRUCT:
                render(g, out, q)
        return
    if t in ("table", "tgroup") and el.find(".//{*}row") is None and el.find(".//row") is None:
        s = inline(el)
        if s: out.append(pre + s)
        return
    if t == "row":
        cells = [U.normspace(" ".join(c.itertext())) for c in el]
        if any(cells): out.append(pre + "| " + " | ".join(cells) + " |")
        return
    for c in el:
        render(c, out, q)

def write_concept(path, pairs, head, body_title, paras, split_chars):
    body = "\n\n".join(paras)
    pairs = pairs + [("checksum", hashlib.sha256(body.encode()).hexdigest())]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if len(body) <= split_chars * 2 or len(paras) < 2:
        md = U.emit_frontmatter(pairs) + "\n" + head + f"# {body_title}\n\n" + body + "\n"
    else:
        chunks, cur, sz = [], [], 0
        for p in paras:
            if cur and sz + len(p) > split_chars:
                chunks.append(cur); cur, sz = [], 0
            cur.append(p); sz += len(p) + 2
        if cur: chunks.append(cur)
        pdir = path[:-3]; os.makedirs(pdir, exist_ok=True)
        for old in os.listdir(pdir):                       # a shorter new version must not keep stale parts
            if old.startswith("part-"): os.remove(os.path.join(pdir, old))
        title = dict(pairs)["title"]
        lk = []
        for k, c in enumerate(chunks, 1):
            pb = "\n\n".join(c); pt = f"{title[:250]} (part {k} of {len(chunks)})"
            pp = [(x, (pt if x == "title" else hashlib.sha256(pb.encode()).hexdigest() if x == "checksum" else v))
                  for x, v in pairs] + [("part_of", f"{k} of {len(chunks)}")]
            open(os.path.join(pdir, f"part-{k:03d}.md"), "w", encoding="utf-8").write(
                U.emit_frontmatter(pp) + f"\n# {body_title}\n\n" + pb + "\n")
            lk.append((f"{os.path.basename(pdir)}/part-{k:03d}.md", pt))
        open(os.path.join(pdir, "index.md"), "w", encoding="utf-8").write(
            f"# {title}\n\n" + "\n".join(f"* [{t}]({os.path.basename(p)})" for p, t in lk) + "\n")
        md = (U.emit_frontmatter(pairs + [("parts", ("raw", str(len(chunks))))]) + "\n" + head +
              "# Parts\n\n" + "\n".join(f"- [{t}]({p})" for p, t in lk) + "\n")
    open(path + ".part", "w", encoding="utf-8").write(md)
    os.replace(path + ".part", path)

def src(url, title, author):
    return ("raw", "\n  - { id: govinfo, resource: " + U.yq(url) + ", title: " + U.yq(title) + ", author: " + U.yq(author) + " }")

def bill_dir(out, congress, btype, num):
    return os.path.join(out, "federal", "legislative", "bills", str(congress), btype, f"{btype}-{num}")

def bill_link(congress, btype, num):
    return f"/federal/legislative/bills/{congress}/{btype}/{btype}-{num}/status.md"

# ----------------------------------------------------------------------------- public laws
def do_plaw(name, data, out, d, run_at, a):
    m = re.match(r"PLAW-(\d+)(publ|pvtl)(\d+)\.xml$", name)
    if not m: return None
    congress, kind, n = m.groups()
    r = ET.fromstring(data)
    meta = r.find("{*}meta")
    title = U.normspace(meta.findtext("{http://purl.org/dc/elements/1.1/}title") or "") if meta is not None else ""
    cites = [U.normspace(c.text or "") for c in (meta.findall("{*}citableAs") if meta is not None else [])]
    approved = (meta.findtext("{*}approvedDate") if meta is not None else "") or ""
    origin = [x.get("href") for x in r.iter() if local(x.tag) == "ref" and (x.get("href") or "").startswith("/us/bill/")][:1]
    paras = []
    main = r.find("{*}main")
    render(main if main is not None else r, paras)
    paras = [p for p in paras if p]
    label = "Public Law" if kind == "publ" else "Private Law"
    pl = f"{label} {congress}-{n}"
    stat = next((c for c in cites if "Stat." in c), "")
    pairs = [("type", label), ("title", (f"{pl}" + (f" ({stat})" if stat else "") + " — " +
                                         re.sub(r"^(Public|Private) Law \d+[–-]\d+:\s*", "", title))[:300]),
             ("description", re.sub(r"^(Public|Private) Law \d+[–-]\d+:\s*", "", title)[:240]),
             ("resource", f"https://www.govinfo.gov/app/details/PLAW-{congress}{kind}{n}"),
             ("tags", ["federal", "legislative", "public-law" if kind == "publ" else "private-law", f"congress-{congress}"]),
             ("citation", pl), ("statutes_at_large", stat), ("congress", congress), ("law_number", n),
             ("enacted_date", approved),
             ("origin_bill", origin[0] if origin else ""),
             ("branch", "legislative"), ("jurisdiction", "united-states"), ("jurisdiction_level", "federal"),
             ("license", "Public domain (U.S. Government work)"), ("status", "stable"),
             ("generated", {"by": TOOL, "at": run_at}),
             ("sources", src(f"{BULK}/{d}/{name}", f"{pl} (USLM XML)", "process:us-gpo-ofr"))]
    head = ""
    if origin:
        mm = re.match(r"/us/bill/(\d+)/(\w+)/(\d+)", origin[0])
        if mm:
            head = f"# Legislative History\n\n- Enacted from [{TYPE_LABEL.get(mm.group(2), mm.group(2))} {mm.group(3)}]({bill_link(*mm.groups())})\n\n"
    stem = f"pl-{congress}-{n}" if kind == "publ" else f"pvtl-{congress}-{n}"
    path = os.path.join(out, "federal", "legislative", "public-laws", congress, stem + ".md")
    write_concept(path, pairs, head, "Text", paras, a.split_chars)
    return path, pairs[1][1]

# ----------------------------------------------------------------------------- bill text
def do_bill(name, data, out, d, run_at, a):
    m = re.match(r"BILLS-(\d+)([a-z]+?)(\d+)([a-z]+)\.xml$", name)
    if not m: return None
    congress, btype, num, ver = m.groups()
    if btype not in TYPE_LABEL: return None
    try:
        r = ET.fromstring(re.sub(rb"<!DOCTYPE[^>]*>", b"", data))
    except ET.ParseError as e:
        print(f"  parse error {name}: {e}", flush=True); return None
    dc = "{http://purl.org/dc/elements/1.1/}"
    dctitle = U.normspace(r.findtext(f".//{dc}title") or "")
    date = U.normspace(r.findtext(f".//{dc}date") or "")
    short = re.sub(r"^\d+\s+[A-Z.]+\s*\d+\s*[A-Z]*\s*:\s*", "", dctitle)
    off = U.normspace(" ".join((r.find(".//official-title") if r.find(".//official-title") is not None else ET.Element("x")).itertext()))
    paras = []
    for part in ("form", "legis-body", "resolution-body", "engrossed-amendment-body", "amendment-doc", "attestation"):
        for e in r.iter(part):
            if part == "form":
                for k in ("legis-type", "official-title"):
                    x = e.find(k)
                    if x is not None and inline(x): paras.append(inline(x))
            else:
                render(e, paras)
    if not paras:
        render(r, paras)
    paras = [p for p in paras if p]
    label = f"{TYPE_LABEL[btype]} {num}"
    vname = VERSIONS.get(ver, ver.upper())
    pairs = [("type", "Bill Text"),
             ("title", f"{label} ({congress}th Congress) — {short or off or 'Bill'} [{vname}]"[:300]),
             ("description", (off or short)[:240]),
             ("resource", f"https://www.govinfo.gov/app/details/BILLS-{congress}{btype}{num}{ver}"),
             ("tags", ["federal", "legislative", "bill", btype, f"congress-{congress}", f"version-{ver}"]),
             ("citation", f"{label}, {congress}th Cong. ({vname})"), ("congress", congress),
             ("bill_type", btype), ("bill_number", num), ("version_code", ver), ("version", vname),
             ("version_date", date),
             ("branch", "legislative"), ("jurisdiction", "united-states"), ("jurisdiction_level", "federal"),
             ("license", "Public domain (U.S. Government work)"), ("status", "draft"),
             ("generated", {"by": TOOL, "at": run_at}),
             ("sources", src(f"{BULK}/{d}/{name}", f"{label} {vname} (bill XML)", "process:us-gpo"))]
    head = f"# Bill\n\n- [Status, actions and summary]({bill_link(congress, btype, num).rsplit('/', 1)[1]})\n\n"
    path = os.path.join(bill_dir(out, congress, btype, num), f"text-{ver}.md")
    write_concept(path, pairs, head, "Text", paras, a.split_chars)
    return path, pairs[1][1]

# ----------------------------------------------------------------------------- bill status
def strip_html(s):
    s = re.sub(r"(?i)</?(p|li|ul|ol|br|div|h\d)\b[^>]*>", "\n\n", s or "")
    return [U.normspace(x) for x in re.split(r"\n\s*\n", html.unescape(re.sub(r"(?s)<[^>]+>", "", s))) if U.normspace(x)]

def do_status(name, data, out, d, run_at, a):
    r = ET.fromstring(data)
    b = r.find("bill")
    if b is None: return None
    congress = b.findtext("congress") or ""
    btype = (b.findtext("type") or b.findtext("billType") or "").lower()
    num = b.findtext("number") or b.findtext("billNumber") or ""
    if btype not in TYPE_LABEL or not num: return None
    label = f"{TYPE_LABEL[btype]} {num}"
    title = U.normspace(b.findtext("title") or "")
    sp = b.find("sponsors/item")
    sponsor = U.normspace(sp.findtext("fullName") or "") if sp is not None else ""
    cos = b.findall("cosponsors/item")
    la = b.find("latestAction")
    latest = f"{la.findtext('actionDate') or ''}: {U.normspace(la.findtext('text') or '')}" if la is not None else ""
    laws = [(x.findtext("type") or "", x.findtext("number") or "") for x in b.findall("laws/item")]
    policy = b.findtext("policyArea/name") or ""
    subjects = [x.findtext("name") for x in b.findall("subjects/legislativeSubjects/item") if x.findtext("name")]
    sums = b.findall("summaries/summary") or b.findall("summaries/billSummaries/item")
    summ = strip_html(sums[-1].findtext("text")) if sums else []
    lines = []
    if laws:
        lines.append("# Became Law\n")
        for lt, ln in laws:
            mm = re.match(r"(\d+)-(\d+)", ln)
            stem = ("pl" if "public" in lt.lower() else "pvtl")
            lines.append(f"- [{lt} {ln}](/federal/legislative/public-laws/{mm.group(1)}/{stem}-{mm.group(1)}-{mm.group(2)}.md)" if mm else f"- {lt} {ln}")
        lines.append("")
    lines += ["# Overview\n", f"- **Bill:** {label}, {congress}th Congress", f"- **Title:** {title}",
              f"- **Sponsor:** {sponsor}" if sponsor else "", f"- **Cosponsors:** {len(cos)}",
              f"- **Introduced:** {b.findtext('introducedDate') or ''}", f"- **Policy area:** {policy}" if policy else "",
              f"- **Latest action:** {latest}" if latest else "",
              f"- **Subjects:** {', '.join(subjects[:40])}" if subjects else "", ""]
    tv = []
    for x in b.findall("textVersions/item"):
        u = x.findtext("formats/item/url") or ""
        mm = re.search(r"BILLS-\d+[a-z]+\d+([a-z]+)", u)
        if mm: tv.append(((x.findtext("date") or "")[:10] + " — " + (x.findtext("type") or mm.group(1)), f"text-{mm.group(1)}.md"))
    if tv:
        lines += ["# Text Versions\n"] + [f"- [{t}]({p})" for t, p in tv] + [""]
    if summ:
        s0 = sums[-1]
        lines += [f"# CRS Summary ({s0.findtext('actionDesc') or ''}, {s0.findtext('actionDate') or ''})\n"] + [p + "\n" for p in summ]
    cm = [U.normspace(x.findtext("name") or "") for x in b.findall("committees/item")]
    if cm:
        lines += ["# Committees\n"] + [f"- {c}" for c in cm if c] + [""]
    acts = b.findall("actions/item")
    if acts:
        lines += ["# Actions\n", "| Date | Action |", "|---|---|"]
        for x in sorted(acts, key=lambda x: (x.findtext("actionDate") or "", x.findtext("actionTime") or "")):
            lines.append(f"| {x.findtext('actionDate') or ''} | {U.normspace(x.findtext('text') or '').replace('|', '/')} |")
        lines.append("")
    rel = b.findall("relatedBills/item")
    if rel:
        lines.append("# Related Bills\n")
        for x in rel:
            rt, rn, rc = (x.findtext("type") or "").lower(), x.findtext("number") or "", x.findtext("congress") or ""
            why = ", ".join(filter(None, (y.findtext("type") for y in x.findall("relationshipDetails/item"))))
            lines.append(f"- [{TYPE_LABEL.get(rt, rt)} {rn} ({rc}th)]({bill_link(rc, rt, rn)}) — {U.normspace(x.findtext('title') or '')}" + (f" ({why})" if why else ""))
        lines.append("")
    body = "\n".join(l for l in lines if l is not None)
    pairs = [("type", "Bill Status"),
             ("title", f"{label} ({congress}th Congress) — {title}"[:300]),
             ("description", (latest or title)[:240]),
             ("resource", b.findtext("legislationUrl") or f"https://www.congress.gov/bill/{congress}th-congress"),
             ("tags", ["federal", "legislative", "bill", btype, f"congress-{congress}"] + (["enacted"] if laws else []) +
                      ([U.slug(policy)] if policy else [])),
             ("citation", f"{label}, {congress}th Cong."), ("congress", congress), ("bill_type", btype), ("bill_number", num),
             ("sponsor", sponsor), ("cosponsor_count", ("raw", str(len(cos)))),
             ("introduced_date", b.findtext("introducedDate") or ""), ("policy_area", policy),
             ("latest_action_date", la.findtext("actionDate") if la is not None else ""),
             ("latest_action", U.normspace(la.findtext("text") or "")[:400] if la is not None else ""),
             ("became_law", ", ".join(f"{t} {n}" for t, n in laws)),
             ("updated", b.findtext("updateDateIncludingText") or b.findtext("updateDate") or ""),
             ("branch", "legislative"), ("jurisdiction", "united-states"), ("jurisdiction_level", "federal"),
             ("license", "Public domain (Library of Congress / CRS bill status data)"),
             ("status", "stable" if laws else "draft"),
             ("generated", {"by": TOOL, "at": run_at}),
             ("sources", src(f"{BULK}/{d}/{name}", f"{label} bill status", "process:library-of-congress-crs")),
             ("checksum", hashlib.sha256(body.encode()).hexdigest())]
    path = os.path.join(bill_dir(out, congress, btype, num), "status.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path + ".part", "w", encoding="utf-8").write(U.emit_frontmatter(pairs) + "\n" + body + "\n")
    os.replace(path + ".part", path)
    return path, pairs[1][1]

# ----------------------------------------------------------------------------- indexes
def nk(s):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s)]

def ftitle(p):
    try:
        m = re.search(r'^title: "(.*)"$', open(p, encoding="utf-8").read(2500), re.M)
        return m.group(1) if m else os.path.basename(p)
    except OSError:
        return os.path.basename(p)

def rebuild_indexes(out, touched):
    leg = os.path.join(out, "federal", "legislative")
    for kind, congress, btype, bdir in sorted(touched):
        if kind == "bill":
            items = sorted(f for f in os.listdir(bdir) if f.endswith(".md") and f != "index.md")
            open(os.path.join(bdir, "index.md"), "w", encoding="utf-8").write(
                f"# {TYPE_LABEL[btype]} {bdir.rsplit('-', 1)[1]} ({congress}th Congress)\n\n" +
                "\n".join(f"* [{ftitle(os.path.join(bdir, f))}]({f})" for f in sorted(items, key=lambda f: (f != "status.md", f))) + "\n")
    tdirs = {(c, t) for k, c, t, _ in touched if k == "bill"}
    for c, t in tdirs:
        tdir = os.path.join(leg, "bills", c, t)
        bills = sorted((b for b in os.listdir(tdir) if os.path.isdir(os.path.join(tdir, b))), key=nk)
        rows = []
        for b in bills:
            s = os.path.join(tdir, b, "status.md")
            rows.append(f"* [{ftitle(s) if os.path.exists(s) else b}]({b}/)")
        open(os.path.join(tdir, "index.md"), "w", encoding="utf-8").write(f"# {TYPE_LABEL[t]} — {c}th Congress\n\n" + "\n".join(rows) + "\n")
    for c in {c for c, _ in tdirs}:
        cdir = os.path.join(leg, "bills", c)
        ts = [t for t in TYPES if os.path.isdir(os.path.join(cdir, t))]
        open(os.path.join(cdir, "index.md"), "w", encoding="utf-8").write(
            f"# Bills and Resolutions — {c}th Congress\n\n" + "\n".join(
                f"* [{TYPE_LABEL[t]}]({t}/) - {sum(1 for x in os.listdir(os.path.join(cdir, t)) if os.path.isdir(os.path.join(cdir, t, x)))} measures" for t in ts) + "\n")
    bills = os.path.join(leg, "bills")
    if os.path.isdir(bills):
        cs = sorted((c for c in os.listdir(bills) if c.isdigit()), key=int, reverse=True)
        open(os.path.join(bills, "index.md"), "w", encoding="utf-8").write(
            "# Bills and Resolutions of Congress\n\nEvery printed version of every bill, with Congress.gov/CRS status, actions and summaries.\n\n" +
            "\n".join(f"* [{c}th Congress]({c}/)" for c in cs) + "\n")
    pls = os.path.join(leg, "public-laws")
    for c in {c for k, c, _, _ in touched if k == "plaw"}:
        cdir = os.path.join(pls, c)
        fs = sorted((f for f in os.listdir(cdir) if f.endswith(".md") and f != "index.md"), key=nk)
        open(os.path.join(cdir, "index.md"), "w", encoding="utf-8").write(
            f"# Laws of the {c}th Congress\n\n" + "\n".join(f"* [{ftitle(os.path.join(cdir, f))}]({f})" for f in fs) + "\n")
    if os.path.isdir(pls):
        cs = sorted((c for c in os.listdir(pls) if c.isdigit()), key=int, reverse=True)
        open(os.path.join(pls, "index.md"), "w", encoding="utf-8").write(
            "# Public and Private Laws\n\nSlip laws as enacted, with Statutes at Large citations (113th Congress-present, USLM XML from GPO).\n\n" +
            "\n".join(f"* [{c}th Congress]({c}/)" for c in cs) + "\n")
    if os.path.isdir(leg):
        names = {"bills": "Bills and Resolutions", "public-laws": "Public and Private Laws"}
        subs = sorted(s for s in os.listdir(leg) if os.path.isdir(os.path.join(leg, s)))
        open(os.path.join(leg, "index.md"), "w", encoding="utf-8").write(
            "# Legislative Branch\n\nWhat Congress is doing and has enacted. Codified law lives in [the U.S. Code](/federal/usc/).\n\n" +
            "\n".join(f"* [{names.get(s, s)}]({s}/)" for s in subs) + "\n")

# ----------------------------------------------------------------------------- main
def congresses(spec):
    if "-" in spec:
        a, b = spec.split("-"); return list(range(int(a), int(b) + 1))
    return [int(x) for x in spec.split(",") if x.strip()]

def current_congress(today=None):
    today = today or dt.date.today()
    c = (today.year - 1789) // 2 + 1
    return c - 1 if (today.year % 2 == 1 and today < dt.date(today.year, 1, 3)) else c

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="legal")
    ap.add_argument("--collections", default="plaw,billstatus,bills")
    ap.add_argument("--congress", default="", help="e.g. 119, 113-119 (default: current Congress)")
    ap.add_argument("--split-chars", type=int, default=6000)
    ap.add_argument("--report", default="", help="append a JSON line per new/changed concept")
    ap.add_argument("--limit", type=int, default=0, help="test mode: files per directory")
    a = ap.parse_args()
    run_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cs = congresses(a.congress) if a.congress else [current_congress()]
    st_dir = os.path.join(a.out, "references", "state"); os.makedirs(st_dir, exist_ok=True)
    rep = open(a.report, "a", encoding="utf-8") if a.report else None
    handlers = {"PLAW": do_plaw, "BILLSTATUS": do_status, "BILLS": do_bill}
    feed = {"PLAW": "public-laws", "BILLSTATUS": "bill-status", "BILLS": "bill-text"}
    stats, touched = Counter(), set()
    for coll in [c.strip().upper() for c in a.collections.split(",") if c.strip()]:
        sp = os.path.join(st_dir, f"govinfo-{coll.lower()}.json")
        state = json.load(open(sp)) if os.path.exists(sp) else {}
        for c in cs:
            try:
                dirs = leaf_dirs(coll, c)
            except urllib.error.HTTPError as e:
                print(f"  {coll} {c}: not available ({e.code})", flush=True); continue
            for d in dirs:
                n = 0
                for name, data in changed_files(coll, d, state, stats):
                    n += 1
                    if a.limit and n > a.limit: break
                    try:
                        res = handlers[coll](name, data, a.out, d, run_at, a)
                    except Exception as e:
                        stats["convert_errors"] += 1; print(f"  convert error {name}: {e}", flush=True); continue
                    if not res:
                        stats["skipped"] += 1; continue
                    path, title = res
                    stats[f"{coll.lower()}_written"] += 1
                    if coll == "PLAW":
                        touched.add(("plaw", str(c), "", ""))
                    else:
                        bd = os.path.dirname(path)
                        touched.add(("bill", os.path.basename(os.path.dirname(os.path.dirname(bd))), os.path.basename(os.path.dirname(bd)), bd))
                    if rep:
                        rep.write(json.dumps({"branch": "legislative", "feed": feed[coll], "path": os.path.relpath(path, a.out), "title": title}) + "\n")
                print(f"  {d}: {n} new/changed files", flush=True)
                if a.limit:
                    state.pop(d, None)                 # test runs never mark a directory as loaded
                json.dump(state, open(sp + ".part", "w")); os.replace(sp + ".part", sp)
    rebuild_indexes(a.out, touched)
    ref = os.path.join(a.out, "references")
    json.dump({"congresses": cs, "collections": a.collections, "at": run_at, "stats": dict(stats)},
              open(os.path.join(ref, "ingest-stats-congress.json"), "w"), indent=1)
    print("done:", json.dumps(dict(stats)), flush=True)

if __name__ == "__main__":
    main()

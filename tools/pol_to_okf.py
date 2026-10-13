#!/usr/bin/env python3
"""
pol_to_okf.py — U.S. agency decisions, opinions and guidance from the Pile of Law dataset
(huggingface.co/datasets/pile-of-law/pile-of-law) -> OKF v0.2 concepts.

LICENSING: the underlying documents are U.S. government works (public domain), but the
Pile of Law compilation is CC BY-NC-SA 4.0. Every concept records both in frontmatter
(`license`, `compilation_license`) so a commercial pipeline can filter, or re-source the
same documents from the agencies directly. Snapshot: collected 2021–2022 — coverage ends
there; documents are point-in-time decisions, so they do not "go stale" the way codes do.

Records: {"text", "created_timestamp", "downloaded_timestamp", "url"}.
Output: <out>/federal/agency/<subset>/<year>/<id>.md (+ indexes, stats).
Usage:  python3 pol_to_okf.py --cache pol_cache --out legal [--subsets nlrb_decisions,olc_memos] [--fetch]
"""
import argparse, ast, datetime as dt, hashlib, json, lzma, os, re, sys, time, urllib.request
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uslm_to_okf as U

TOOL = "pol_to_okf/1.0"
UA = "OKF-LawCorpus/1.0 (open legal-knowledge research; contact " + os.environ.get("LEGAL_US_CONTACT", "see github.com/MetatronAngelAgent/legal-united-states") + ")"
BASE = "https://huggingface.co/datasets/pile-of-law/pile-of-law/resolve/main/data"

# subset -> (folder, publisher/agency, document type)
SUBSETS = {
    "olc_memos":                ("doj-olc-opinions",        "U.S. Department of Justice, Office of Legal Counsel", "Legal Opinion"),
    "doj_guidance":             ("doj-guidance",            "U.S. Department of Justice", "Guidance Document"),
    "nlrb_decisions":           ("nlrb-decisions",          "National Labor Relations Board", "Agency Decision"),
    "irs_legal_advice_memos":   ("irs-legal-advice-memos",  "Internal Revenue Service, Office of Chief Counsel", "Legal Advice Memorandum"),
    "taxrulings":               ("us-tax-court-opinions",   "United States Tax Court", "Tax Court Opinion"),  # Pile of Law's "taxrulings" is Tax Court memos/opinions
    "eoir":                     ("doj-eoir-decisions",      "Executive Office for Immigration Review (BIA/AG)", "Agency Decision"),
    "ftc_advisory_opinions":    ("ftc-advisory-opinions",   "Federal Trade Commission", "Advisory Opinion"),
    "sec":                      ("sec-administrative",      "U.S. Securities and Exchange Commission", "Agency Decision"),
    "cfpb_cc":                  ("cfpb-guidance",           "Consumer Financial Protection Bureau", "Guidance Document"),
    "dol_ecab":                 ("dol-ecab-decisions",      "U.S. Department of Labor, Employees' Compensation Appeals Board", "Agency Decision"),
    "fmshrc":                   ("fmshrc-decisions",        "Federal Mine Safety and Health Review Commission", "Agency Decision"),
    "hhs_alj":                  ("hhs-alj-decisions",       "U.S. Department of Health and Human Services, ALJ / DAB", "Agency Decision"),
    "medicaid_policy_guidance": ("cms-medicaid-guidance",   "Centers for Medicare & Medicaid Services", "Guidance Document"),
    "ed_policy_guidance":       ("ed-policy-guidance",      "U.S. Department of Education", "Guidance Document"),
    "oig":                      ("oig-reports",             "Offices of Inspector General", "Inspector General Report"),
    "acus_reports":             ("acus-reports",            "Administrative Conference of the United States", "Report"),
    "scotus_oral":              ("scotus-oral-arguments",   "Supreme Court of the United States", "Oral Argument Transcript"),
    "bva":                      ("bva-decisions",           "Board of Veterans' Appeals", "Agency Decision"),
}
SPLITS = ["train", "validation"]

def files_for(subset):
    if subset in ("bva",):
        return [f"{s}.{subset}.jsonl.xz" for s in SPLITS]
    return [f"{s}.{subset}.jsonl.xz" for s in SPLITS]

def fetch(subset, cache):
    os.makedirs(cache, exist_ok=True)
    for fn in files_for(subset):
        p = os.path.join(cache, fn)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            continue
        req = urllib.request.Request(f"{BASE}/{fn}", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=1800) as r, open(p + ".part", "wb") as out:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                out.write(b)
        os.replace(p + ".part", p)
        print(f"  fetched {fn} ({os.path.getsize(p)/1e6:.0f} MB)", flush=True)
        time.sleep(1)

def fix_text(t):
    """Some Pile of Law subsets store text as a Python bytes literal: "b'...\\n...'" with
    escaped newlines and raw UTF-8 byte escapes. Decode those back to real text."""
    s = (t or "").strip()
    if len(s) > 3 and s[0] == "b" and s[1] in "'\"" and s[-1] == s[1]:
        try:
            return ast.literal_eval(s).decode("utf-8", errors="replace")
        except Exception:
            try:
                return s[2:-1].encode("latin-1", "backslashreplace").decode("unicode_escape").encode("latin-1", "replace").decode("utf-8", "replace")
            except Exception:
                return t
    return t

def parse_date(s):
    s = (s or "").strip()
    for fmt in ("%m-%d-%Y", "%Y-%m-%d", "%Y", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(s[:10] if fmt != "%Y" else s[:4], fmt).date()
        except ValueError:
            pass
    m = re.search(r"\b(19|20)\d{2}\b", s)
    return dt.date(int(m.group(0)), 1, 1) if m else None

MONTHS = r"(January|February|March|April|May|June|July|August|September|October|November|December|Jan\.|Feb\.|Mar\.|Apr\.|Aug\.|Sept?\.|Oct\.|Nov\.|Dec\.)"
SKIP_RE = re.compile(r"^(" + MONTHS + r"\s+\d{1,2},?\s+\d{4}\.?|\d{1,2}/\d{1,2}/\d{2,4}|page \d+.*|\d+|"
                     r"memorandum( opinion)?( for| to)?.*|u\.?s\.? department of justice.*|office of legal counsel.*|"
                     r"washington,? d\.?c\.?.*|united states (government|of america).*|opinion|decision|order|"
                     r"(before|in the matter of)\b.*:?|to:.*|from:.*|re:?|subject:?|date:.*|"
                     r"(the )?(deputy |assistant |acting )*(attorney general|general counsel)\b.*|"
                     r"(division|bureau|office|department) of\b.*|.*@\S+\.(gov|com|org).*|"
                     r"(receipt|direct dial|telephone|fax|tel\.?)\b.*)$", re.I)

SENT_START = re.compile(r"^(The|A|An|In|Under|As|This|It|Although|Because|We|You|Our|Section|Pursuant|For|When|If)\b")
ABBREV_END = re.compile(r"(\b[A-Z]\.|U\.S\.C\.|U\.S\.|et seq\.|No\.|Nos\.|v\.|Inc\.|Co\.|Corp\.)$")

def title_from(text, fallback):
    lines = [U.normspace(l) for l in (text or "").splitlines()[:80]]
    # letters/advisory opinions: the "Re:" subject line is the title
    for i, l in enumerate(lines):
        m = re.match(r"^(Re|RE|Subject|SUBJECT)\s*:\s*(.*)$", l)
        if m:
            t, j = m.group(2), i + 1
            while j < len(lines) and (not t or (lines[j] and len(t) < 180)):
                if lines[j] and re.match(r"^Dear\b", lines[j]):
                    break
                t = (t + " " + lines[j]).strip() if lines[j] else t
                j += 1
                if t and j < len(lines) and not lines[j]:
                    break
            if len(t) >= 8:
                return t[:220]
    def ok(l, allow_caps):
        if not (12 <= len(l) <= 220) or re.fullmatch(r"[\W\d_]+", l) or SKIP_RE.match(l):
            return False
        if re.match(r"^\d+[-–]\d+\s", l) or len(l.split()) < 3:
            return False
        letters = re.sub(r"[^A-Za-z]", "", l)
        return allow_caps or not (letters.isupper() and len(letters) > 6)
    for allow_caps in (False, True):
        for i, l in enumerate(lines):
            if not ok(l, allow_caps):
                continue
            t, j = l, i + 1
            while j < len(lines) and lines[j] and len(t) < 160 and j - i <= 3 \
                    and not (re.search(r"[.?!:]$", t) and not ABBREV_END.search(t)) \
                    and not SKIP_RE.match(lines[j]) \
                    and not (len(t.split()) >= 6 and SENT_START.match(lines[j])):
                t += " " + lines[j]; j += 1
            return t.rstrip(".:—-– ")
    return fallback

TEXT_QUALITY = {
    "nlrb_decisions": "OCR text with many inter-word spaces missing (as published in the source dataset)",
    "taxrulings": "normalized by the source dataset: lowercased, punctuation removed",
}

def route(subset, text):
    """Per-record (folder, agency, doc type) override for mixed subsets."""
    if subset == "taxrulings":
        head = re.sub(r"_", " ", (text or "")[:800].lower())
        if re.search(r"tax court", head) and re.search(r"\bv\.?\s+commissioner", head):
            return ("us-tax-court-opinions", "United States Tax Court", "Tax Court Opinion")
        return ("irs-written-determinations", "Internal Revenue Service",
                "IRS Written Determination (PLR / TAM / Chief Counsel Advice)")
    return None

def special_title(subset, text):
    """Per-subset title extraction for sources whose first lines are not usable."""
    head = (text or "")[:1500]
    if subset == "nlrb_decisions":
        case = re.search(r"Cases?\s*(?:Nos?\.?)?\s*((?:[0-9A-Za-z]{1,3}\s*[-–]\s*)?[A-Z]{1,3}\s*[-–]\s*\d{1,6}(?:\s*[-–]\s*\d+)?)", head)
        if case:
            pre = head[:case.start()]
            pre = re.sub(r"^.*?(In\s*the\s*Matter\s*of|RELATIONS\s*BOARD|R\S*\s*BOARD)", "", pre, flags=re.S | re.I)
            pre = re.sub(r"^[\s\d\-–.,]+", "", pre)
            parties = re.sub(r"-(?=[a-z])", "", pre)
            parties = re.sub(r"(?<=[a-z.,])(and)(?=[A-Z])", r" \1 ", parties)
            parties = re.sub(r"(?<=[A-Z]{3})(and)(?=[A-Z]{3})", r" \1 ", parties)
            parties = re.sub(r",(?=\S)", ", ", parties)
            parties = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", parties)
            parties = U.normspace(parties).strip(" ,.;:")
            cid = re.sub(r"\s+", "", case.group(1))
            d = re.search(MONTHS + r"\s*\d{1,2},\s*\d{4}", head)
            if 6 <= len(parties) <= 260:
                return f"{parties} — NLRB Case {cid}" + (f" ({d.group(0)[-4:]})" if d else "")
            return f"NLRB Case {cid}" + (f" ({d.group(0)[-4:]})" if d else "")
    if subset == "taxrulings":
        t = re.sub(r"_", " ", head)
        m = re.search(r"(?:tax court\s+)?(.{3,160}?)\s+petitioners?\s+v\.?\s+commissioner of internal revenue", t)
        dk = re.search(r"docket nos?\.?\s*(\d[\w\-]*)", t)          # must contain a digit (not "filed")
        memo = re.search(r"\bt\s*c\s*memo\b", t)
        if not m and not re.search(r"tax court", t):
            ident = re.search(r"\b(cca|plr|tam|fsa|tl|ilm|pmta|sca|gl|cc)[-\s]?(\d{5,6}[-\s]?\d{2})\b", t)
            subj = re.search(r"\bsubject\s*:?\s*([a-z][^-]{8,120}?)(?:\s{2,}|\s-{3,}|$)", t)
            kind = ("Chief Counsel Advice" if ident and ident.group(1) == "cca" else
                    "Private Letter Ruling" if "ruling letter" in t or "private letter ruling" in t else
                    "IRS Written Determination")
            label = f"{kind} {ident.group(1).upper()}-{re.sub(r'[^0-9]', '', ident.group(2))[:6]}-{re.sub(r'[^0-9]', '', ident.group(2))[6:]}" if ident else kind
            if subj:
                return f"{label} — {subj.group(1).strip().capitalize()}"
            uil = re.search(r"uniform issue list\s+([\d.\-]+)", t)
            return label + (f" (UIL {uil.group(1)})" if uil else "")
        if m:
            name = re.sub(r"^.*?\btax court\s+", "", m.group(1)).strip()
            name = " ".join(w.capitalize() if w not in ("and", "of", "the") else w for w in name.split())
            return f"{name} v. Commissioner" + (f" — Docket No. {dk.group(1).upper()}" if dk else "") + \
                   (" (T.C. Memo.)" if memo else "")
    return None

def desc_from(paras, fallback):
    for p in paras[:40]:
        if len(p) >= 120 and not SKIP_RE.match(p):
            return (p[:237] + "…") if len(p) > 240 else p
    return fallback

def paragraphs(text):
    paras = [U.normspace(p) for p in re.split(r"\n\s*\n", text or "")]
    out = []
    for p in paras:
        if not p:
            continue
        # very long blobs with single newlines: split on sentence-ish boundaries every ~1500 chars
        while len(p) > 4000:
            cut = p.rfind(". ", 0, 3000)
            cut = cut + 1 if cut > 500 else 3000
            out.append(p[:cut].strip()); p = p[cut:].strip()
        out.append(p)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", default="legal")
    ap.add_argument("--subsets", default=",".join(k for k in SUBSETS if k != "bva"))
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--split-chars", type=int, default=6000)
    ap.add_argument("--delete-after", action="store_true", help="remove each .xz after converting (saves disk)")
    a = ap.parse_args()
    run_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    root = os.path.join(a.out, "federal", "agency")
    totals = {}
    for subset in [s.strip() for s in a.subsets.split(",") if s.strip()]:
        folder, agency, dtype = SUBSETS[subset]
        if a.fetch:
            fetch(subset, a.cache)
        idx = defaultdict(list)
        folder_meta = {}
        seen = set()
        n = dup = empty = garbled = 0
        for fn in files_for(subset):
            p = os.path.join(a.cache, fn)
            if not os.path.exists(p):
                print(f"  missing {fn}"); continue
            with lzma.open(p, "rt", encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    text = fix_text(rec.get("text") or "")
                    if len(text.strip()) < 200:
                        empty += 1; continue
                    sample = [ch for ch in text[:3000] if not ch.isspace() and ch not in "-_"]   # redactions ok
                    if sum(ch.isalpha() for ch in sample) / max(1, len(sample)) < 0.50:
                        garbled += 1; continue          # unreadable OCR / font-encoding garbage (letters < 50% of glyphs)
                    h = hashlib.sha256(text.encode()).hexdigest()
                    if h in seen:
                        dup += 1; continue
                    seen.add(h)
                    url = rec.get("url") or ""
                    d = parse_date(rec.get("created_timestamp"))
                    year = str(d.year) if d else "undated"
                    base = re.sub(r"\.(pdf|htm|html|txt)$", "", url.rstrip("/").split("/")[-1], flags=re.I)
                    docid = (U.slug(base)[:60] if base and base.lower() not in ("download", "index", "") else "") or h[:12]
                    docid = f"{docid}-{h[:6]}"
                    r_folder, r_agency, r_dtype = route(subset, text) or (folder, agency, dtype)
                    title = special_title(subset, text) or title_from(text, f"{r_agency} — {docid}")
                    paras = paragraphs(text)
                    body = "\n\n".join(paras)
                    pairs = [
                        ("type", r_dtype), ("title", title[:240]),
                        ("description", desc_from(paras, f"{r_dtype} — {r_agency}" + (f", {d.isoformat()}" if d else ""))),
                        ("resource", url or f"https://huggingface.co/datasets/pile-of-law/pile-of-law ({subset})"),
                        ("tags", ["federal", "agency", r_folder]),
                        ("agency", r_agency), ("document_date", d.isoformat() if d else ""),
                        ("jurisdiction", "united-states"), ("jurisdiction_level", "federal"),
                        ("license", "Underlying document: U.S. government work (public domain)"),
                        ("compilation_license", "CC BY-NC-SA 4.0 (Pile of Law) — non-commercial; re-source from the agency for commercial use"),
                        ("dataset_snapshot", (rec.get("downloaded_timestamp") or "2021-2022")),
                        ("text_quality", TEXT_QUALITY.get(subset, "")),
                        ("status", "stable"),
                        ("generated", {"by": TOOL, "at": run_at}),
                        ("sources", ("raw", "\n  - { id: pile-of-law, resource: " +
                                     U.yq(f"https://huggingface.co/datasets/pile-of-law/pile-of-law/blob/main/data/{fn}") +
                                     ", title: " + U.yq(f"Pile of Law — {subset}") +
                                     ", author: " + U.yq("process:pile-of-law") + " }" +
                                     ("\n  - { id: original, resource: " + U.yq(url) +
                                      ", title: " + U.yq(f"Original publication ({agency})") + " }" if url else ""))),
                        ("checksum", h),
                    ]
                    ddir = os.path.join(root, r_folder, year)
                    folder_meta[r_folder] = (r_agency, r_dtype)
                    os.makedirs(ddir, exist_ok=True)
                    path = os.path.join(ddir, f"{docid}.md")
                    if len(body) <= a.split_chars or len(paras) < 2:
                        open(path, "w", encoding="utf-8").write(U.emit_frontmatter(pairs) + "\n# Text\n\n" + body + "\n")
                    else:
                        chunks, cur, sz = [], [], 0
                        for pg in paras:
                            if cur and sz + len(pg) > a.split_chars:
                                chunks.append(cur); cur, sz = [], 0
                            cur.append(pg); sz += len(pg) + 2
                        if cur:
                            chunks.append(cur)
                        pdir = path[:-3]; os.makedirs(pdir, exist_ok=True)
                        lk = []
                        for i, c in enumerate(chunks, 1):
                            pb = "\n\n".join(c); pt = f"{title[:200]} (part {i} of {len(chunks)})"
                            pp = [(k, (pt if k == "title" else hashlib.sha256(pb.encode()).hexdigest() if k == "checksum" else v))
                                  for k, v in pairs] + [("part_of", f"{i} of {len(chunks)}")]
                            open(os.path.join(pdir, f"part-{i:03d}.md"), "w", encoding="utf-8").write(
                                U.emit_frontmatter(pp) + "\n# Text\n\n" + pb + "\n")
                            lk.append((f"{docid}/part-{i:03d}.md", pt))
                        with open(os.path.join(pdir, "index.md"), "w", encoding="utf-8") as fi:
                            fi.write(f"# {title[:200]}\n\n" + "\n".join(f"* [{t}]({os.path.basename(x)})" for x, t in lk) + "\n")
                        open(path, "w", encoding="utf-8").write(
                            U.emit_frontmatter(pairs + [("parts", str(len(chunks)))]) + "\n# Text\n\n" + paras[0] +
                            "\n\n# Parts\n\n" + "\n".join(f"- [{t}]({x})" for x, t in lk) + "\n")
                    idx[(r_folder, year)].append((f"{docid}.md", title[:160]))
                    n += 1
            if a.delete_after:
                os.remove(p)
        for fold, (ag, dty) in folder_meta.items():
            sdir = os.path.join(root, fold)
            os.makedirs(sdir, exist_ok=True)
            years = sorted(y for (f2, y) in idx if f2 == fold)
            for y in years:
                with open(os.path.join(sdir, y, "index.md"), "w", encoding="utf-8") as f:
                    f.write(f"# {ag} — {y}\n\n" + "\n".join(f"* [{t}]({p})" for p, t in sorted(idx[(fold, y)])) + "\n")
            open(os.path.join(sdir, "index.md"), "w", encoding="utf-8").write(
                f"# {ag} — {dty}s\n\nSource: Pile of Law `{subset}` (snapshot 2021–2022; compilation CC BY-NC-SA 4.0).\n\n" +
                "\n".join(f"* [{y}]({y}/) - {len(idx[(fold, y)])} documents" for y in years) + "\n")
        totals[subset] = {"documents": n, "duplicates_skipped": dup, "too_short_skipped": empty,
                          "unreadable_skipped": garbled}
        print(f"{subset}: {n} documents ({dup} duplicates, {empty} too short, {garbled} unreadable)", flush=True)

    os.makedirs(root, exist_ok=True)
    existing = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    names = {v[0]: v[1] for v in SUBSETS.values()}
    names["irs-written-determinations"] = "Internal Revenue Service — written determinations"
    open(os.path.join(root, "index.md"), "w", encoding="utf-8").write(
        "# Federal Agency Decisions, Opinions & Guidance\n\n" +
        "\n".join(f"* [{names.get(d, d)}]({d}/)" for d in existing) + "\n")
    ref = os.path.join(a.out, "references"); os.makedirs(ref, exist_ok=True)
    sp = os.path.join(ref, "ingest-stats-agency.json")
    old = json.load(open(sp)) if os.path.exists(sp) else {}
    old.update(totals)
    json.dump(old, open(sp, "w"), indent=1)

if __name__ == "__main__":
    main()

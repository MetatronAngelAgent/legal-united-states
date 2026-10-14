#!/usr/bin/env python3
"""
pro_codes_to_okf.py — state codes released into the public domain by Public.Resource.Org
(structured HTML by UniCourt, published on GitHub) -> OKF v0.2 concepts.

Fills the statute gap for states whose official code is otherwise only reachable through a
publisher's public-access portal: Arkansas, Georgia, Mississippi, Tennessee. These are the newest
bulk releases that exist; each concept says how current it is (`currency`, `stale_after`), because
amendments enacted after the release date are NOT reflected.

Source repos (public domain, "NO RIGHTS RESERVED"):
  https://github.com/UniCourt/cic-code-ar  transforms/ar/ocar/r78   Release 78, 2020-11-06
  https://github.com/UniCourt/cic-code-ga  transforms/ga/ocga/r86   Release 86, 2022-11
  https://github.com/UniCourt/cic-code-ms  transforms/ms/ocms/r78   Release 78, 2020-07-08 (MS ended its electronic edition)
  https://github.com/UniCourt/cic-code-tn  transforms/tn/octn/r76   Release 76, 2021-05-21
Fetched with a shallow sparse git checkout of only the release directory (~140-220 MB per state).

Output: <out>/<state>/statutes/title-T/chapter-C/[subchapter-|article-|part-|subpart-]/sec-<n>.md
        long annotation blocks -> sec-<n>--annotations.md companions; indexes; stats report.
Usage:  python3 pro_codes_to_okf.py --cache pro_cache --out legal [--states ar,ga,ms,tn]
"""
import argparse, datetime as dt, hashlib, json, os, re, subprocess, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uslm_to_okf as U
try:
    from bs4 import BeautifulSoup, NavigableString, Tag
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "beautifulsoup4", "lxml"])
    from bs4 import BeautifulSoup, NavigableString, Tag

TOOL = "pro_codes_to_okf/1.0"
STATES = {
    "ar": dict(name="arkansas", label="Arkansas", repo="cic-code-ar", rel="transforms/ar/ocar/r78",
               release="Release 78 of the Official Code of Arkansas Annotated", date="2020-11-06",
               cite="Ark. Code Ann. § {n}", code="Arkansas Code of 1987 Annotated", raw="gov.ar.code/release78.2020.11.06"),
    "ga": dict(name="georgia", label="Georgia", repo="cic-code-ga", rel="transforms/ga/ocga/r86",
               release="Release 86 of the Official Code of Georgia Annotated", date="2022-11-01",
               cite="O.C.G.A. § {n}", code="Official Code of Georgia Annotated", raw=None),
    "ms": dict(name="mississippi", label="Mississippi", repo="cic-code-ms", rel="transforms/ms/ocms/r78",
               release="Release 78 of the Mississippi Code of 1972 Annotated (final electronic release)", date="2020-07-08",
               cite="Miss. Code Ann. § {n}", code="Mississippi Code of 1972 Annotated", raw="gov.ms.code.ann.2018/release78.2020.07.08"),
    "tn": dict(name="tennessee", label="Tennessee", repo="cic-code-tn", rel="transforms/tn/octn/r76",
               release="Release 76 of the Tennessee Code Annotated", date="2021-05-21",
               cite="Tenn. Code Ann. § {n}", code="Tennessee Code Annotated", raw="gov.tn.tca/release76.2021.05.21"),
}
NUM = r"\d+[A-Z]?(?:\.\d+)?-\d+[A-Z]?(?:\.\d+)?-\d+[A-Z]?(?:\.\d+)*"
SEC_HEAD = re.compile(r"^(?:§+\s*)?(" + NUM + r")\b\s*(?:[.,—–-]\s*)?(.*)$")
LEVEL = {"t": "title", "st": "subtitle", "c": "chapter", "sc": "subchapter", "a": "article", "p": "part", "sp": "subpart"}
NOTE_START = re.compile(
    r"^(History[.:]|HISTORY[.:]|Acts \d{4}\b|Code \d{4}\b|Ga\. L\. \d{4}|Laws,? \d{4}|Orig\. Code|"
    r"Cross[- ]?[Rr]eferences?\b|Editor[’']?s [Nn]otes?\b|Compiler[’']?s [Nn]otes?\b|Amendments?\.|Amendment Notes\b|"
    r"Effective Dates?\.|Law reviews?\.|Code Commission [Nn]otes?\b|A\.C\.R\.C\. Notes\b|Publisher[’']?s [Nn]otes?\b|"
    r"Research References\b|Case Notes\b|JUDICIAL DECISIONS\b|NOTES TO DECISIONS\b|OPINIONS OF THE ATTORNEY GENERAL\b|"
    r"Joint Legislative Committee Note\b|Federal Aspects\b|Attorney General Opinions\b|Textbooks\.|"
    r"Collateral [Rr]eferences\b|Comparative Legislation\b|RESEARCH REFERENCES\b|Repeal(?:ed|s)? [Nn]otes?\b)")
HIST_START = re.compile(r"^(History[.:]|HISTORY[.:]|Acts \d{4}\b|Code \d{4}\b|Ga\. L\. \d{4}|Laws,? \d{4}|Orig\. Code)")

def ws(s):
    return re.sub(r"\s+", " ", s).strip()

def fetch(cache, st):
    S = STATES[st]
    d = os.path.join(cache, S["repo"])
    src = os.path.join(d, S["rel"])
    if os.path.isdir(src) and any(f.endswith(".html") for f in os.listdir(src)):
        return src
    if not os.path.isdir(os.path.join(d, ".git")):
        subprocess.check_call(["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--no-checkout",
                               f"https://github.com/UniCourt/{S['repo']}", d])
    subprocess.check_call(["git", "-C", d, "sparse-checkout", "set", "--no-cone", f"/{S['rel']}/"])
    subprocess.check_call(["git", "-C", d, "checkout", "-q"])
    return src

_RAW = {}
def raw_soup(cache, st, fn):
    """Public.Resource.Org's own HTML conversion of the same release (archive.org) — used only for
    sections whose text the standardized UniCourt HTML lost."""
    S = STATES[st]
    if not S.get("raw"):
        return None
    key = (st, fn)
    if key not in _RAW:
        d = os.path.join(cache, "archive-raw", st); os.makedirs(d, exist_ok=True)
        lp = os.path.join(d, fn)
        if not os.path.exists(lp):
            import urllib.request
            ua = "OKF-LawCorpus/1.0 (open legal-knowledge research; contact " + os.environ.get(
                "LEGAL_US_CONTACT", "see github.com/MetatronAngelAgent/legal-united-states") + ")"
            try:
                req = urllib.request.Request(f"https://archive.org/download/{S['raw']}/{fn}", headers={"User-Agent": ua})
                with urllib.request.urlopen(req, timeout=600) as r:
                    open(lp + ".part", "wb").write(r.read())
                os.replace(lp + ".part", lp)
            except Exception as e:
                print(f"  raw fallback unavailable for {fn}: {e}", flush=True)
                _RAW[key] = None
                return None
        _RAW.clear()                                      # keep one title in memory
        _RAW[key] = BeautifulSoup(open(lp, encoding="utf-8", errors="replace").read(), "lxml")
    return _RAW[key]

def split_id(hid, num):
    """'t16c07a03p3As16-7-58' -> [('title','16'),('chapter','07'),('article','03'),('part','3A')]"""
    pre = hid
    m = re.search(r"s" + re.escape(num) + r"[A-Za-z0-9.\-]*$", hid)
    if m:
        pre = hid[:m.start()]
    out, acc = [], ""
    for k, v in re.findall(r"(st|sc|sp|t|c|a|p)(\d+[A-Z]?)", pre):
        acc += k + v
        out.append((LEVEL[k], v, acc))
    return out

class Renderer:
    def __init__(self, resolve):
        self.resolve = resolve            # href -> bundle path or None

    def inline(self, el):
        out = []
        for c in el.children:
            if isinstance(c, NavigableString):
                out.append(str(c))
            elif isinstance(c, Tag):
                if c.name in ("ol", "ul"):
                    continue
                if c.name == "a" and c.get("href"):
                    t = ws(c.get_text(" "))
                    p = self.resolve(c["href"])
                    out.append(f"[{t}]({p})" if p and t else t)
                elif c.name == "br":
                    out.append(" ")
                else:
                    out.append(self.inline(c))
        return re.sub(r"\s+([,.;:)])", r"\1", ws("".join(out)))

    def lst(self, ol, depth, parent_id):
        lines, n = [], 0
        typ = ol.get("type") or ("a" if "alpha" in (ol.get("class") or []) else "1")
        for li in ol.find_all("li", recursive=False):
            n += 1
            lid = li.get("id") or ""
            lab = None
            if parent_id and lid.startswith(parent_id):
                lab = lid[len(parent_id):]
            elif re.search(r"ol\d+([A-Za-z0-9]+)$", lid):
                lab = re.search(r"ol\d+([A-Za-z0-9]+)$", lid).group(1)
            if lab and not re.fullmatch(r"[a-z]{1,4}|[A-Z]{1,4}|\d{1,3}(?:\.\d+)?", lab):
                # some generated ids append the label to the previous sibling's id ("...ol11d2" = item 2)
                t = re.search(r"([a-z]{1,4}|[A-Z]{1,4}|\d{1,3})$", lid)
                lab = t.group(1) if t else None
            if not lab or len(lab) > 6:
                lab = (chr(96 + n) if typ == "a" else chr(64 + n) if typ == "A" else str(n)) if n <= 26 else str(n)
            if ol.name == "ul":
                lines.append("  " * depth + "- " + self.inline(li))
            else:
                lines.append("  " * depth + f"({lab}) " + self.inline(li))
            for sub in li.find_all(["ol", "ul"], recursive=False):
                lines += self.lst(sub, depth + 1, lid)
        return lines

    def block(self, el):
        """-> list of markdown paragraphs"""
        if el.name in ("ol", "ul"):
            return ["\n".join(self.lst(el, 0, None))]
        if el.name in ("h4", "h5", "h6"):
            t = ws(el.get_text(" "))
            return [f"## {t}"] if t else []
        if el.name in ("div", "section", "nav"):
            if el.name == "nav":
                return []
            out = []
            for c in el.children:
                if isinstance(c, Tag):
                    out += self.block(c)
                elif ws(str(c)):
                    out.append(ws(str(c)))
            return out
        if el.name == "table":
            rows = [[ws(td.get_text(" ")).replace("|", "\\|") for td in tr.find_all(["td", "th"])] for tr in el.find_all("tr")]
            rows = [r for r in rows if any(r)]
            if not rows:
                return []
            w = max(len(r) for r in rows)
            rows = [r + [""] * (w - len(r)) for r in rows]
            return ["\n".join(["| " + " | ".join(rows[0]) + " |", "|" + "---|" * w] + ["| " + " | ".join(r) + " |" for r in rows[1:]])]
        t = self.inline(el)
        return [t] if t else []

def convert_state(st, src, out, run_at, split_chars, cache):
    S = STATES[st]
    files = sorted((f for f in os.listdir(src) if re.search(r"\.title\.[\w.]+\.html$", f)),
                   key=lambda f: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", f)])
    root = os.path.join(out, S["name"], "statutes")
    pages = f"https://unicourt.github.io/{S['repo']}/{S['rel']}"
    # pass 1: locate every section -> path, so cross-references can become bundle links
    parsed, where, names, used = [], {}, {}, set()
    for fn in files:
        soup = BeautifulSoup(open(os.path.join(src, fn), encoding="utf-8").read(), "lxml")
        h1 = soup.find("h1")
        tname = ws(h1.get_text(" ")) if h1 else fn
        secs = []
        for h in soup.find_all(["h2", "h3"]):
            hid = h.get("id") or ""
            t = ws(h.get_text(" "))
            m = SEC_HEAD.match(t) if h.name == "h3" else None
            if not m:
                if hid:
                    names[hid] = t
                continue
            num = m.group(1)
            levels = split_id(hid, num) if hid else []
            if not levels or levels[0][0] != "title":
                tn = re.search(r"\.title\.([\w.]+)\.html$", fn).group(1)
                levels = [("title", tn, "")] + [l for l in levels if l[0] != "title"]
            ddir = os.path.join(root, *[f"{k}-{U.slug(v.lstrip('0') or '0')}" for k, v, _ in levels])
            base = f"sec-{U.slug(num)}"
            path, k = os.path.join(ddir, base + ".md"), 2
            while path in used:
                path, k = os.path.join(ddir, f"{base}-v{k}.md"), k + 1
            used.add(path)
            where.setdefault(num, path)
            if hid:
                where.setdefault("#" + hid, path)
            secs.append((h, hid, num, m.group(2), levels, path))
        parsed.append((fn, soup, tname, secs))

    def resolver(cur):
        def r(href):
            frag = href.split("#", 1)[1] if "#" in href else ""
            p = where.get("#" + frag) if frag else None
            if not p and frag:
                m = re.search(r"s(" + NUM + r")$", frag)
                p = where.get(m.group(1)) if m else None
            return os.path.relpath(p, os.path.dirname(cur)).replace(os.sep, "/") if p else None
        return r

    idx, stats, total, notes_split = defaultdict(list), {}, 0, 0
    from collections import Counter
    stats_fallback = Counter()
    for fn, soup, tname, secs in parsed:
        written = 0
        for h, hid, num, rest, levels, path in secs:
            R = Renderer(resolver(path))
            heading = rest.strip().rstrip(".") or "(untitled)"
            text, hist, notes, mode = [], [], [], "text"
            for sib in h.find_next_siblings():
                if sib.name in ("h1", "h2", "h3"):
                    break
                paras = R.block(sib)
                if not paras:
                    continue
                first = re.sub(r"^## ", "", paras[0])
                if mode == "text" and (sib.name in ("h4", "h5") or NOTE_START.match(first)):
                    mode = "hist" if HIST_START.match(first) else "notes"
                elif mode == "hist" and (sib.name in ("h4", "h5") or (NOTE_START.match(first) and not HIST_START.match(first))):
                    mode = "notes"
                {"text": text, "hist": hist, "notes": notes}[mode].extend(paras)
            text_source = "standardized"
            if not text and hid:
                # 1) the body's list items exist elsewhere in the standardized file (ids keep the section id)
                tops = []
                for li in soup.find_all("li", id=re.compile("^" + re.escape(hid) + r"ol\d")):
                    ol = li.find_parent(["ol", "ul"])
                    while ol is not None and ol.find_parent(["ol", "ul"]) is not None and \
                            (ol.find_parent("li") or {}).get("id", "").startswith(hid):
                        ol = ol.find_parent(["ol", "ul"])
                    if ol is not None and ol not in tops:
                        tops.append(ol)
                for ol in tops:
                    text += R.block(ol)
                if text:
                    text_source = "standardized (relocated list)"
                else:
                    # 2) Public.Resource.Org's own conversion of the same release
                    rs = raw_soup(cache, st, fn)
                    rh = rs.find(id=hid) if rs is not None else None
                    if rh is not None:
                        for sib in rh.find_next_siblings():
                            if sib.name in ("h1", "h2", "h3"):
                                break
                            paras = R.block(sib)
                            if paras and (sib.name in ("h4", "h5") or NOTE_START.match(re.sub(r"^## ", "", paras[0]))):
                                break
                            text += [p for p in paras if p]
                        if text:
                            text_source = "archive.org Public.Resource.Org HTML (standardized file lacked the text)"
                stats_fallback[text_source] += 1
            body = "\n\n".join(text).strip()
            repealed = bool(re.search(r"\[(Repealed|Reserved|Transferred|Obsolete|Expired|Superseded|Renumbered|Redesignated)|"
                                     r"^(Repealed|Reserved)\b|\b(Repealed|Reserved|Redesignated|Superseded|Renumbered)\.?\]?$", heading)) \
                and len(body) < 400
            if not body:
                body = "(No statutory text in this release" + ("; see heading." if repealed else ".") + ")"
            cite = S["cite"].format(n=num)
            hier = " / ".join([tname] + [names.get(raw) or f"{k.title()} {v.lstrip('0') or '0'}" for k, v, raw in levels[1:]])
            url = f"{pages}/{fn}" + (f"#{hid}" if hid else "")
            pairs = [
                ("type", "Statute Section"), ("title", f"{cite} — {heading}"), ("description", heading),
                ("resource", url),
                ("tags", [S["name"], "statutes", "state-law", f"title-{levels[0][1].lstrip('0') or '0'}", "historical-snapshot"]),
                ("citation", cite), ("jurisdiction", S["name"]), ("jurisdiction_level", "state"),
                ("hierarchy", hier), ("edition", f"{S['release']} ({S['date']})"),
                ("currency", f"Current through {S['date']} only. Later amendments are NOT reflected; verify against the official code."),
                ("stale_after", S["date"]),
                ("status", "deprecated" if repealed else "stable"),
                ("license", "Public domain (edict of government; Public.Resource.Org/UniCourt release, no rights reserved)"),
                ("text_source", text_source if text_source != "standardized" else ""),
                ("generated", {"by": TOOL, "at": run_at}),
                ("sources", ("raw", "\n  - { id: pro-unicourt, resource: " + U.yq(f"https://github.com/UniCourt/{S['repo']}/tree/HEAD/{S['rel']}") +
                             ", title: " + U.yq(f"{S['code']} — {S['release']}, structured HTML") +
                             ", author: " + U.yq("process:public-resource-org") + " }")),
                ("checksum", hashlib.sha256(body.encode()).hexdigest()),
            ]
            extra = ""
            if hist:
                extra += "\n# History\n\n" + "\n\n".join(hist) + "\n"
            notes_md = "\n\n".join(notes)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if notes and len(notes_md) > split_chars * 3:
                apath = path[:-3] + "--annotations.md"
                ap = [(k, (f"{cite} — Notes and annotations" if k == "title" else "Annotation" if k == "type" else
                           hashlib.sha256(notes_md.encode()).hexdigest() if k == "checksum" else v)) for k, v in pairs]
                open(apath, "w", encoding="utf-8").write(U.emit_frontmatter(ap) + "\n# Notes and annotations\n\n" + notes_md + "\n")
                extra += f"\n# Notes and annotations\n\nSee [{cite} — Notes and annotations]({os.path.basename(apath)}).\n"
                notes_split += 1
            elif notes:
                extra += "\n# Notes and annotations\n\n" + notes_md + "\n"
            open(path, "w", encoding="utf-8").write(U.emit_frontmatter(pairs) + "\n# Text\n\n" + body + "\n" + extra)
            idx[os.path.dirname(path)].append((os.path.basename(path), f"{cite} — {heading}"))
            written += 1
        stats[fn] = {"title": tname, "sections": written}
        total += written
        print(f"{st} {fn}: {written}", flush=True)

    def nk(s):
        return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s)]
    alldirs = set(idx)
    for d in list(alldirs):
        while len(d) > len(root):
            d = os.path.dirname(d); alldirs.add(d)
    alldirs.add(root)
    for d in sorted(alldirs):
        subs = sorted((x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x))), key=nk)
        items = sorted(idx.get(d, []), key=lambda t: nk(t[0]))
        rel = os.path.relpath(d, os.path.join(out, S["name"])).replace(os.sep, " / ")
        head = f"# {S['label']} — {rel}\n\n"
        if d == root:
            head += (f"{S['code']}, {S['release']} ({S['date']}). Public domain, from Public.Resource.Org's bulk release "
                     f"(structured by UniCourt). **Historical snapshot: amendments after {S['date']} are not reflected.**\n\n")
        open(os.path.join(d, "index.md"), "w", encoding="utf-8").write(
            head + "\n".join([f"* [{s}]({s}/)" for s in subs] + [f"* [{t}]({f})" for f, t in items]) + "\n")
    sdir = os.path.join(out, S["name"])
    kinds = sorted(x for x in os.listdir(sdir) if os.path.isdir(os.path.join(sdir, x)))
    open(os.path.join(sdir, "index.md"), "w", encoding="utf-8").write(
        f"# {S['label']}\n\n" + "\n".join(f"* [{k.replace('-', ' ').title()}]({k}/)" for k in kinds) + "\n")
    print(f"{st} empty-text fallbacks: {dict(stats_fallback)}", flush=True)
    return {"release": S["release"], "date": S["date"], "sections": total, "annotation_companions": notes_split,
            "empty_text_fallbacks": dict(stats_fallback), "files": stats}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", default="legal")
    ap.add_argument("--states", default="ar,ga,ms,tn")
    ap.add_argument("--split-chars", type=int, default=6000)
    a = ap.parse_args()
    os.makedirs(a.cache, exist_ok=True)
    run_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rep = {}
    for st in [s.strip() for s in a.states.split(",") if s.strip()]:
        rep[st] = convert_state(st, fetch(a.cache, st), a.out, run_at, a.split_chars, a.cache)
        print(f"== {st}: {rep[st]['sections']} sections", flush=True)
    ref = os.path.join(a.out, "references"); os.makedirs(ref, exist_ok=True)
    json.dump(rep, open(os.path.join(ref, "ingest-stats-pro-codes.json"), "w"), indent=1)

if __name__ == "__main__":
    main()

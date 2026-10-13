#!/usr/bin/env python3
"""
sdmc_to_okf.py — San Diego Municipal Code (City Clerk PDFs) -> OKF v0.2 concepts.

Reads the cache produced by sdmc_fetch.py (one PDF per Article/Division), extracts text
with poppler's pdftotext, strips running headers/footers, splits on section markers
(§52.1001), and writes one concept per section under
  <out>/california/municipal/san-diego/chapter-<C>/article-<A>/division-<D>/sec-<N>.md
with the same frontmatter contract as the state/federal layers, plus index.md files,
a log.md entry, and references/gaps-san-diego.md.

Validation: every "§" section marker found in the extracted text must yield a concept
(written == markers, per document), and empty bodies are reported.

Requires: pdftotext (poppler-utils). Otherwise stdlib only.
"""
import argparse, datetime as dt, hashlib, json, os, re, subprocess, sys
from collections import defaultdict

TOOL = "sdmc_to_okf/1.0"
JUR_DIR = ("california", "municipal", "san-diego")

# A section marker is "§52.1001" alone on its line, or followed by whitespace + a capitalized
# heading. Table cross-references such as "§157.0111(b)" are NOT section starts.
SEC_RE = re.compile(r"^\x01§\s*(\d+\.\d+(?:\.\d+)?[A-Za-z]?)(?:\s*$|\s+(.*)$)")
# In -layout text a true section start sits at the left margin with its heading on the same
# row ("§156.0301      Purpose and Applicability"). Table cross-references sit at the end of
# rows or deep in the indentation, or carry a subsection suffix ("§156.0315(e)").
RAW_MARK_RE = re.compile(r"^(\s{0,20})§\s*(\d+\.\d+(?:\.\d+)?[A-Za-z]?)(?:\s*$|\s{2,}(\S.*)$|\s([A-Z“\"].*)$)")
HDR_RE = [re.compile(r"^San Diego Municipal Code\s*$"),
          re.compile(r"^Chapter \d+[A-Za-z]?:\s"),
          re.compile(r"^\(\d{1,2}-\d{4}\)\s*$")]
FOOT_RE = re.compile(r"^Ch\.\s*Art\.\s*Div\.\s*$")
NUMLINE_RE = re.compile(r"^\d+(\.\d+)?[A-Za-z]?\s*$")
ENUM_RE = re.compile(r"^\(([0-9]{1,3}|[a-zA-Z]{1,4}|[ivxlc]{1,6})\)\s*$")
HIST_RE = re.compile(r"^\((“|\"|Added|Amended|Retitled|Renumbered|Repealed|Formerly|"
                     r"Effective|Operative|New|Incorporated)", re.I)
ORD_RE = re.compile(r"\bO[–-]\s?\d{3,6}")

def normspace(s):
    return re.sub(r"[ \t\r\f\v]+", " ", (s or "").replace(" ", " ")).strip()

def slug(s):
    s = normspace(s).lower().replace(".", "-")
    s = re.sub(r"[^a-z0-9\-]+", "-", s)
    return re.sub(r"-{2,}", "-", s).strip("-") or "x"

def yq(s):
    s = str(s).replace("\\", "\\\\").replace('"', '\\"')
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", s).replace("\n", " ")
    return f'"{s}"'

def frontmatter(pairs):
    out = ["---"]
    for k, v in pairs:
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, list):
            out.append(f"{k}: [{', '.join(yq(x) for x in v)}]")
        elif isinstance(v, dict):
            out.append(f"{k}: {{ {', '.join(f'{a}: {yq(b)}' for a, b in v.items())} }}")
        elif isinstance(v, tuple) and v[0] == "raw":
            out.append(f"{k}: {v[1]}")
        else:
            out.append(f"{k}: {yq(v)}")
    return "\n".join(out + ["---"]) + "\n"

def parse_docname(fn):
    """Ch05Art02Division10.pdf -> ('5','2','10'); Ch05Art02.5AppendixA -> ('5','2.5','appendix-a')."""
    b = os.path.basename(fn)[:-4]
    m = re.match(r"Ch(\d+)Art(\d+(?:\.\d+)?)(.*)$", b, re.I)
    if not m:
        m2 = re.match(r"Ch(\d+)(.*)$", b, re.I)
        return (m2.group(1).lstrip("0") or "0", None, slug(m2.group(2)) or None) if m2 else (None, None, slug(b))
    ch, art, rest = m.group(1).lstrip("0") or "0", m.group(2), m.group(3)
    art = art.lstrip("0") if not art.startswith("0.") else art
    d = re.match(r"Division(\d+(?:\.\d+)?[A-Za-z]?)$", rest, re.I)
    div = (d.group(1).lstrip("0") or "0") if d else (slug(rest) or None)
    return ch, art or "0", div

def extract(pdf):
    r = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", pdf, "-"], capture_output=True)
    return r.stdout.decode("utf-8", errors="replace")

def clean_lines(text):
    """Drop running headers/footers, strip indentation, tag true section starts with \\x01,
    and fold wrapped section headings (rows immediately under the marker row) into the marker.
    Returns (lines, edition, chapter_title)."""
    lines = text.replace("\f", "\n").splitlines()
    out, edition, chtitle = [], None, None
    i = 0
    while i < len(lines):
        raw = lines[i].rstrip()
        m = RAW_MARK_RE.match(raw)
        if m:
            num = m.group(2)
            head = (m.group(3) or m.group(4) or "").strip()
            i += 1
            # heading continuation: only when the heading row is visibly unfinished —
            # it ends on a connector/punctuation, or it is a full-width row followed by a short
            # row and then a blank line. Body text at the same indent is never absorbed.
            CONT = re.compile(r"(,|;|—|–|-|\b(and|or|of|to|the|for|in|on|at|with|by|from|a|an))$", re.I)
            while i < len(lines) and lines[i].strip() and head and not RAW_MARK_RE.match(lines[i]) \
                    and not FOOT_RE.match(lines[i].strip()):
                nxt = lines[i].strip()
                blank_after = (i + 1 >= len(lines)) or not lines[i + 1].strip()
                if CONT.search(head) or (len(head) >= 68 and len(nxt) < 60 and blank_after):
                    head += " " + nxt; i += 1
                else:
                    break
            out.append(f"\x01§{num}" + (f" {normspace(head)}" if head else ""))
            continue
        s = raw.strip()
        if s.startswith("San Diego Municipal Code"):
            mt = re.search(r"(Chapter \d+[A-Za-z]?:.*)$", s)
            if mt:
                chtitle = chtitle or normspace(mt.group(1))
            i += 1; continue
        if FOOT_RE.match(s):
            i += 1
            while i < len(lines) and (not lines[i].strip() or
                                      re.fullmatch(r"[\d.\sA-Za-z]{1,40}", lines[i].strip()) and
                                      re.search(r"\d", lines[i]) and len(lines[i].split()) <= 6):
                i += 1
                if i < len(lines) and lines[i - 1].strip():
                    break
            continue
        if HDR_RE[0].match(s):
            i += 1; continue
        if HDR_RE[1].match(s):
            chtitle = chtitle or s
            i += 1; continue
        if HDR_RE[2].match(s):
            edition = edition or s.strip("()")
            i += 1; continue
        if s == "\f" or s.startswith("\f"):
            s = s.lstrip("\f")
        out.append(s)
        i += 1
    return out, edition, chtitle

def joinwrap(parts):
    """Join wrapped lines; a line ending in a hyphen continues the same word/compound."""
    out = ""
    for p in parts:
        if out.endswith("-") and not out.endswith(" -"):
            out += p
        else:
            out = f"{out} {p}" if out else p
    return out

def paragraphs(lines):
    """Join wrapped lines into paragraphs; merge bare enumerators into the next paragraph."""
    paras, cur = [], []
    for s in lines:
        if not s:
            if cur:
                paras.append(joinwrap(cur)); cur = []
            continue
        if HIST_RE.match(s) and cur:          # history notes always start a new paragraph
            paras.append(joinwrap(cur)); cur = []
        cur.append(s)
    if cur:
        paras.append(joinwrap(cur))
    merged, pending = [], None
    for p in paras:
        p = normspace(p)
        if ENUM_RE.match(p):
            pending = (pending + " " if pending else "") + p
            continue
        merged.append(f"{pending} {p}" if pending else p)
        pending = None
    if pending:
        merged.append(pending)
    return merged

def split_history(paras):
    """Trailing paragraphs that are ordinance-history parentheticals -> history list."""
    body, hist = list(paras), []
    while body and HIST_RE.match(body[-1]) and ORD_RE.search(body[-1]):
        hist.insert(0, body.pop())
    # history notes can also be interleaved right after the heading; pull those too
    kept = []
    for p in body:
        if HIST_RE.match(p) and ORD_RE.search(p) and p.endswith(")") and len(p) < 500:
            hist.append(p)
        else:
            kept.append(p)
    return kept, hist

class Writer:
    def __init__(self, out, run_at, stale, split_chars):
        self.out, self.run_at, self.stale, self.split_chars = out, run_at, stale, split_chars
        self.index = defaultdict(list)
        self.heads = {}
        self.stats = defaultdict(int)
        self.gaps = []
        self.seen = set()

    def put(self, path, md, title, desc):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w", encoding="utf-8").write(md)
        self.index[os.path.dirname(path)].append((os.path.basename(path), title, normspace(desc)[:160]))
        self.stats["files"] += 1

    def render(self, num, heading, body, hist, src_url, edition, title, part=None, nparts=None):
        chap = self.cur_chapter
        repealed = bool(re.search(r"\bRepealed\b|\[Reserved\]|\bReserved\b", heading or "", re.I)) or \
                   body.startswith("(This section has been repealed") or \
                   (not body.strip() and any("Repealed" in h for h in hist))
        cite = f"San Diego Mun. Code § {num}"
        desc = heading or (body[:200] if body else cite)
        pairs = [
            ("type", "Municipal Code Section"),
            ("title", title),
            ("description", desc[:240]),
            ("resource", src_url),
            ("tags", ["california", "municipal", "san-diego", f"sdmc-chapter-{chap}"]),
            ("citation", cite),
            ("jurisdiction", "san-diego"),
            ("jurisdiction_level", "municipal"),
            ("parent_jurisdiction", "california"),
            ("code_edition", edition),
            ("status", "deprecated" if repealed else "stable"),
            ("stale_after", self.stale),
            ("generated", {"by": TOOL, "at": self.run_at}),
            ("sources", ("raw", "\n  - { id: sd-clerk, resource: " + yq(src_url) +
                         ", title: " + yq("San Diego Municipal Code, Office of the City Clerk" +
                                          (f" ({edition} edition)" if edition else "")) +
                         ", author: " + yq("process:san-diego-city-clerk") + " }")),
            ("checksum", hashlib.sha256(body.encode()).hexdigest()),
        ]
        if part:
            pairs.append(("part", f"{part[0]} of {part[1]}"))
        if nparts:
            pairs.append(("parts", str(nparts)))
        md = [frontmatter(pairs), "# Text", "", body, ""]
        if hist and not part:
            md += ["# History", ""] + [f"- {h}" for h in hist] + [""]
        return "\n".join(md)

    def section(self, ddir, num, heading, paras, hist, src_url, edition):
        path = os.path.join(ddir, f"sec-{slug(num)}.md")
        if path in self.seen:
            for k in range(2, 50):
                alt = os.path.join(ddir, f"sec-{slug(num)}--v{k}.md")
                if alt not in self.seen:
                    self.gaps.append(f"duplicate section marker § {num} -> {alt}")
                    path = alt; break
        self.seen.add(path)
        title = f"San Diego Mun. Code § {num}" + (f" — {heading}" if heading else "")
        repealed_only = not paras and any("(Repealed" in h for h in hist)
        if repealed_only:
            paras = ["(This section has been repealed; no operative text remains.)"]
            self.stats["repealed_sections"] += 1
        body = "\n\n".join(paras)
        if not paras and not re.search(r"Repealed|Reserved", heading or "", re.I):
            self.gaps.append(f"heading only in source PDF (no text): § {num} ({os.path.basename(src_url)})")
            self.stats["empty_bodies"] += 1
        if len(body) > self.split_chars and len(paras) > 1:
            chunks, cur, n = [], [], 0
            for p in paras:
                if cur and n + len(p) > self.split_chars:
                    chunks.append(cur); cur, n = [], 0
                cur.append(p); n += len(p) + 2
            if cur:
                chunks.append(cur)
            stem, links = path[:-3], []
            for i, c in enumerate(chunks, 1):
                pp = f"{stem}--part-{i}.md"; pt = f"{title} (part {i} of {len(chunks)})"
                self.put(pp, self.render(num, heading, "\n\n".join(c), [], src_url, edition, pt,
                                         part=(i, len(chunks))), pt, c[0])
                links.append((os.path.basename(pp), pt))
            parent = paras[0] + "\n\n# Parts\n\n" + "\n".join(f"- [{t}]({p})" for p, t in links)
            self.put(path, self.render(num, heading, parent, hist, src_url, edition, title,
                                       nparts=len(chunks)), title, heading or paras[0])
            self.stats["split_sections"] += 1
        else:
            self.put(path, self.render(num, heading, body, hist, src_url, edition, title),
                     title, heading or (paras[0] if paras else ""))
        self.stats["sections"] += 1

    def document(self, ddir, fn, url, edition, lines, art_h, div_h, chapter):
        """Whole-document concept for divisions/appendices with no numbered sections."""
        base = os.path.basename(fn)[:-4]
        body_lines = [s for s in lines if s not in (art_h, div_h)]
        paras, hist = split_history(paragraphs(body_lines))
        # a trailing "(Repealed ...)" without ordinance pattern still counts as history
        extra = [p for p in paras if p.startswith("(")]
        paras = [p for p in paras if not p.startswith("(")]
        hist = list(dict.fromkeys(hist + extra))
        repealed = any("(Repealed" in h for h in hist) and not paras
        label = div_h or art_h or base
        heading = re.sub(r"\s+", " ", label)
        is_appendix = "appendix" in base.lower()
        name = (slug(re.sub(r"^.*?(Appendix)", r"\1", base)) if is_appendix else "division-document")
        path = os.path.join(ddir, f"{name}.md")
        title = f"San Diego Municipal Code — {heading}"
        self.cur_chapter = chapter
        body = "\n\n".join(paras) if paras else ("(This division has been repealed; no operative text remains.)"
                                                  if repealed else "")
        md = self.render(f"{chapter} ({base})", heading, body, hist, url, edition, title)
        md = md.replace('type: "Municipal Code Section"',
                        'type: "Municipal Code Appendix"' if is_appendix else 'type: "Municipal Code Division"', 1)
        md = md.replace('citation: "San Diego Mun. Code § ' + f"{chapter} ({base})" + '"',
                        f'citation: "San Diego Mun. Code, {heading}"', 1)
        if repealed:
            md = md.replace('status: "stable"', 'status: "deprecated"', 1)
        self.put(path, md, title, heading)
        self.stats["document_concepts"] += 1
        if repealed:
            self.stats["repealed_divisions"] += 1
        elif not paras:
            self.gaps.append(f"no text extracted: {base} (scanned image or table?)")

    def write_indexes(self, root):
        alldirs = set(self.index)
        for d in list(alldirs):
            p = d
            while len(p) > len(root):
                alldirs.add(os.path.dirname(p)); p = os.path.dirname(p)
        kids = defaultdict(set)
        for d in alldirs:
            if d != root:
                kids[os.path.dirname(d)].add(d)
        # make sure california/ and the bundle root list what exists on disk
        for d in (root, os.path.join(root, "california"), os.path.join(root, "california", "municipal")):
            alldirs.add(d)
            if os.path.isdir(d):
                for e in os.listdir(d):
                    p = os.path.join(d, e)
                    if os.path.isdir(p) and e != "references":
                        kids[d].add(p)
        for d in sorted(alldirs):
            # never clobber state-layer indexes we don't own, except the shared parents
            if d.startswith(os.path.join(root, "california", "codes")):
                continue
            lines = ['---\nokf_version: "0.2"\n---\n'] if d == root else []
            h = self.heads.get(d) or os.path.basename(d).replace("-", " ").title() or "Bundle"
            lines.append(f"# {h}\n")
            for s in sorted(kids.get(d, set())):
                sh = self.heads.get(s) or os.path.basename(s).replace("-", " ").title()
                lines.append(f"* [{sh}]({os.path.basename(s)}/) - {sh}")
            if kids.get(d):
                lines.append("")
            for fn, t, ds in sorted(self.index.get(d, [])):
                lines.append(f"* [{t}]({fn})" + (f" - {ds}" if ds else ""))
            open(os.path.join(d, "index.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
            self.stats["indexes"] += 1

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True, help="sdmc_fetch.py cache dir (has manifest.json)")
    ap.add_argument("--out", default="ok")
    ap.add_argument("--split-chars", type=int, default=6000)
    a = ap.parse_args()
    run_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=90)).strftime("%Y-%m-%dT00:00:00Z")
    man = json.load(open(os.path.join(a.cache, "manifest.json")))
    w = Writer(a.out, run_at, stale, a.split_chars)
    jroot = os.path.join(a.out, *JUR_DIR)
    per_doc = []

    for ch in sorted(man, key=lambda c: int(c)):
        for doc in man[ch]:
            fn, url = doc.get("file"), doc["url"]
            if not fn or not os.path.exists(fn):
                w.gaps.append(f"not downloaded: {url} ({doc.get('error', 'missing')})")
                continue
            c, art, div = parse_docname(fn)
            w.cur_chapter = c
            ddir = os.path.join(jroot, f"chapter-{c}")
            if art:
                ddir = os.path.join(ddir, f"article-{slug(art)}")
            if div:
                ddir = os.path.join(ddir, f"division-{slug(div)}" if re.match(r"^\d", div) else div)
            text = extract(fn)
            lines, edition, chtitle = clean_lines(text)
            if chtitle:
                w.heads[os.path.join(jroot, f"chapter-{c}")] = chtitle
            # pre-section lines: article/division headings
            first = next((i for i, s in enumerate(lines) if SEC_RE.match(s)), len(lines))  # header region
            pre = [s for s in lines[:first] if s]
            art_h = next((s for s in pre if s.startswith("Article")), None)
            div_h = None
            for idx, s in enumerate(pre):
                if s.startswith("Division"):
                    div_h = s
                    for nxt in pre[idx + 1:]:
                        if not div_h.endswith(",") or nxt.startswith("("):
                            break
                        div_h += " " + nxt
                    break
            if art and art_h:
                w.heads[os.path.dirname(ddir) if div else ddir] = art_h
            if div and div_h:
                w.heads[ddir] = div_h
            # Layout-tagged markers are section starts unless the number belongs to a different
            # chapter/article (a margin cross-reference); those stay in the body as text.
            prefix = f"{c}{art}" if (c and art and re.match(r"^\d", art)) else None
            def numkey(n):
                return tuple(int(x) for x in re.findall(r"\d+", n))
            starts, last = [], ()
            for idx, s in enumerate(lines):
                m = SEC_RE.match(s)
                if not m:
                    continue
                n = m.group(1)
                if prefix and not (n.startswith(prefix + ".") or n == prefix):
                    w.stats["xref_markers_skipped"] += 1
                    continue
                starts.append(idx)
            markers = len(starts)
            written_before = w.stats["sections"]
            for si, i in enumerate(starts):
                m = SEC_RE.match(lines[i])
                num, inline_head = m.group(1), (m.group(2) or "").strip()
                j = starts[si + 1] if si + 1 < len(starts) else len(lines)
                block = lines[i + 1:j]
                heading = inline_head
                paras = paragraphs(block)
                paras, hist = split_history(paras)
                w.section(ddir, num, normspace(heading), paras, hist, url, edition)
            got = w.stats["sections"] - written_before
            per_doc.append((os.path.basename(fn), markers, got))
            if markers == 0:
                w.document(ddir, fn, url, edition, lines, art_h, div_h, c)
            elif got != markers:
                w.gaps.append(f"count mismatch {os.path.basename(fn)}: markers {markers} written {got}")
        print(f"  chapter {ch}: cumulative {w.stats['sections']} sections", flush=True)

    w.heads[a.out] = "Open Knowledge: United States Law"
    w.heads[os.path.join(a.out, "california")] = "California"
    w.heads[os.path.join(a.out, "california", "municipal")] = "California Municipal Codes"
    w.heads[jroot] = "San Diego Municipal Code"
    w.write_indexes(a.out)

    logp = os.path.join(a.out, "log.md")
    old = re.sub(r"^# [^\n]*\n+", "", open(logp, encoding="utf-8").read(), count=1) if os.path.exists(logp) else ""
    open(logp, "w", encoding="utf-8").write(
        "# Bundle Update Log\n\n## " + run_at[:10] +
        f"\n* **Creation**: Added the [San Diego Municipal Code](/california/municipal/san-diego/index.md) "
        f"({w.stats['sections']} sections from {len(per_doc)} City Clerk documents) via {TOOL}.\n\n" + old)

    ref = os.path.join(a.out, "references"); os.makedirs(ref, exist_ok=True)
    tot_m = sum(m for _, m, _ in per_doc); tot_g = sum(g for _, _, g in per_doc)
    rep = ["# Gaps report — San Diego Municipal Code", "", f"Run: {run_at}", "",
           f"- Documents processed: {len(per_doc)}",
           f"- Section markers found: {tot_m} · concepts written: {tot_g}",
           f"- Split (multi-part) sections: {w.stats['split_sections']}",
           f"- In-text § cross-references kept as body text (not section starts): {w.stats['xref_markers_skipped']}",
           f"- Repealed sections (kept, status: deprecated): {w.stats['repealed_sections']}",
           f"- Empty bodies: {w.stats['empty_bodies']}",
           f"- Unnumbered documents written as single concepts: {w.stats['document_concepts']} "
           f"({w.stats['repealed_divisions']} are repealed divisions)", ""]
    if w.gaps:
        rep += ["## Issues"] + [f"- {g}" for g in w.gaps]
    open(os.path.join(ref, "gaps-san-diego.md"), "w", encoding="utf-8").write("\n".join(rep) + "\n")
    json.dump(dict(w.stats), open(os.path.join(ref, "ingest-stats-san-diego.json"), "w"), indent=2)
    print(f"done: {len(per_doc)} docs, markers {tot_m}, written {tot_g}, files {w.stats['files']}, "
          f"empty {w.stats['empty_bodies']}, gaps {len(w.gaps)}", flush=True)

if __name__ == "__main__":
    main()

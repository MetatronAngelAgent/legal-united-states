#!/usr/bin/env python3
"""
uslm_to_okf.py — United States Code (USLM XML bulk release) -> Open Knowledge Format (OKF v0.2) bundle.

Converts the official OLRC release-point bulk download (xml_uscAll@<congress>-<pl>.zip,
from uscode.house.gov/download/releasepoints/...) into OKF concepts: one markdown file
per USC section under <out>/federal/usc/title-<T>/<hierarchy>/sec-<N>.md, with the same
frontmatter contract as the California layer (citation / jurisdiction / provenance /
lifecycle / checksum), per-directory index.md files, and root log.md updates.

USLM notes:
- Each <section> carries identifier="/us/usc/t18/s1030", <num>, <heading>, and nested
  <subsection>/<paragraph>/<subparagraph>/... blocks, each with <num>/<chapeau>/<content>.
- <ref href="/us/usc/t15/s1681"> cross-references are rewritten as bundle-relative
  markdown links, which gives the OKF citation graph for free.
- <sourceCredit> becomes the # History section. The large editorial <notes> blocks
  (amendment notes etc.) are intentionally skipped in v1 to keep concepts chunk-sized;
  the `resource` URL links to the full official text.
- @status="repealed|transferred|..." (and "Repealed." headings) -> status: deprecated.

Usage:
  python3 uslm_to_okf.py --dump xml_uscAll@119-120.zip --out ok                 # all titles
  python3 uslm_to_okf.py --dump xml_uscAll@119-120.zip --out ok --titles 18,26 # subset

Safe to run into the same bundle as pubinfo_to_okf.py output: it only (re)writes
federal/ paths, the root index.md (listing whatever top-level dirs exist), and log.md.
Stdlib only.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict

TOOL_ACTOR = "uslm_to_okf/1.0"
OKF_VERSION = "0.2"

LEVELS = ["subtitle", "chapter", "subchapter", "part", "subpart", "division", "subdivision"]
BLOCKS = {"subsection", "paragraph", "subparagraph", "clause", "subclause",
          "item", "subitem", "subsubitem", "chapeau", "content", "continuation", "p"}

# ---------------------------------------------------------------------------

def local(tag):
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""

def normspace(s):
    return re.sub(r"[ \t\r\f\v]+", " ", (s or "").replace(" ", " ")).strip()

def slug(s):
    s = normspace(s).rstrip(".").lower().replace(".", "-")
    s = re.sub(r"[^a-z0-9\-]+", "-", s)
    return re.sub(r"-{2,}", "-", s).strip("-") or "x"

def yq(s):
    s = str(s).replace("\\", "\\\\").replace('"', '\\"')
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", s).replace("\n", " ")
    return f'"{s}"'

def emit_frontmatter(pairs):
    out = ["---"]
    for k, v in pairs:
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, list):
            out.append(f"{k}: [{', '.join(yq(x) for x in v)}]")
        elif isinstance(v, dict):
            out.append(f"{k}: {{ {', '.join(f'{ik}: {yq(iv)}' for ik, iv in v.items())} }}")
        elif isinstance(v, tuple) and v[0] == "raw":
            out.append(f"{k}: {v[1]}")
        else:
            out.append(f"{k}: {yq(v)}")
    out.append("---")
    return "\n".join(out) + "\n"

def usc_link(href):
    """'/us/usc/t15/s1681' -> '/federal/usc/title-15/sec-1681.md' (flat-form link;
    consumers tolerate broken/moved links per OKF §6)."""
    m = re.match(r"^/us/usc/t(\d+[a-zA-Z]?)/s([0-9a-zA-Z.\-]+)", href or "")
    if not m:
        return None
    return f"/federal/usc/title-{m.group(1).lower()}/sec-{slug(m.group(2))}.md"

# ---------------------------------------------------------------------------
# Section subtree -> markdown paragraphs
# ---------------------------------------------------------------------------

def section_text(sec):
    """Render a <section> element to (heading, paragraphs, source_credit, status)."""
    heading = ""
    credit = ""
    paras = []

    def inline(el, out):
        """Flatten an element's inline content to text, rewriting refs, dropping footnotes."""
        t = local(el.tag)
        if t == "note":                      # inline footnote bodies: drop
            return
        if t == "ref":
            if "footnoteRef" in (el.get("class") or ""):
                return                       # drop footnote markers
            href = el.get("href", "")
            inner = "".join(_collect_inline(el))
            link = usc_link(href)
            out.append(f"[{normspace(inner)}]({link})" if link and normspace(inner)
                       else normspace(inner))
            return
        if el.text:
            out.append(el.text)
        for ch in el:
            inline(ch, out)
            if ch.tail:
                out.append(ch.tail)

    def _collect_inline(el):
        bits = [el.text or ""]
        for ch in el:
            if local(ch.tag) != "note":
                bits.extend(_collect_inline(ch))
            bits.append(ch.tail or "")
        return bits

    def walk(el, prefix=""):
        nonlocal heading, credit
        t = local(el.tag)
        if t == "heading" and not heading:
            heading = normspace("".join(_collect_inline(el)))
            return
        if t == "sourcecredit":
            credit = normspace("".join(_collect_inline(el)))
            return
        if t in ("notes", "toc"):            # editorial notes: skipped (see docstring)
            return
        if t in ("chapeau", "content", "continuation", "p"):
            out = []
            inline(el, out)
            txt = normspace("".join(out))
            if txt:
                paras.append((prefix + " " + txt).strip())
            return
        # structural block with its own <num>: push the enumerator onto the prefix
        if t in BLOCKS or t == "section":
            num = ""
            kids = list(el)
            for ch in kids:
                if local(ch.tag) == "num":
                    num = normspace("".join(_collect_inline(ch)))
                    break
            child_prefix = num if t != "section" else ""
            first = True
            for ch in kids:
                ct = local(ch.tag)
                if ct == "num":
                    continue
                if ct in ("chapeau", "content", "continuation", "p"):
                    out = []
                    inline(ch, out)
                    txt = normspace("".join(out))
                    if txt:
                        paras.append(((child_prefix + " " if first and child_prefix else "") + txt).strip())
                        first = False
                else:
                    walk(ch, child_prefix if first else "")
                    if ct in BLOCKS:
                        first = False
            return
        for ch in el:
            walk(ch, prefix)

    walk(sec)
    status = (sec.get("status") or "").lower()
    if not status and re.search(r"\bRepealed\b|\bOmitted\b|\bTransferred\b|\bRenumbered\b|\bVacant\b|\bReserved\b", heading or "", re.I):
        status = "repealed"
    return heading, paras, credit, status

# ---------------------------------------------------------------------------

class FederalWriter:
    def __init__(self, out, run_at, dump_label, stale_after, split_chars):
        self.out, self.run_at = out, run_at
        self.dump_label, self.stale_after = dump_label, stale_after
        self.split_chars = split_chars
        self.index = defaultdict(list)
        self.dirmeta = {}
        self.stats = defaultdict(int)
        self.gaps = []
        self.seen = set()

    def src_raw(self):
        return ("raw", "\n  - { id: olrc-releasepoint, resource: " +
                yq("https://uscode.house.gov/download/releasepoints/ (" + self.dump_label + ")") +
                ", title: " + yq("US Code USLM XML bulk release (" + self.dump_label + ")") +
                ", author: " + yq("process:us-house-olrc") + " }")

    def write_section(self, tnum, hier, secnum, heading, paras, credit, status):
        tdir = os.path.join(self.out, "federal", "usc", f"title-{tnum}")
        d = os.path.join(tdir, *[f"{lv}-{slug(nm)}" for lv, nm in hier])
        path = os.path.join(d, f"sec-{slug(secnum)}.md")
        if path in self.seen:
            for i in range(2, 50):
                alt = os.path.join(d, f"sec-{slug(secnum)}--v{i}.md")
                if alt not in self.seen:
                    self.gaps.append(f"duplicate: {tnum} USC {secnum} -> {alt}")
                    path = alt
                    break
        self.seen.add(path)

        app = tnum.endswith("a")
        cite = (f"{tnum[:-1]} U.S.C. App. § {secnum}" if app
                else f"{tnum.upper()} U.S.C. § {secnum}" if not tnum.isdigit()
                else f"{tnum} U.S.C. § {secnum}")
        title_line = f"{cite} — {heading}" if heading else cite
        body = "\n\n".join(paras)
        desc = heading or (paras[0][:200] if paras else "")
        if not paras and not status:
            self.gaps.append(f"empty body: {cite}")
            self.stats["empty_bodies"] += 1

        if len(body) > self.split_chars and len(paras) > 1:
            self._split(path, tnum, cite, title_line, desc, paras, credit, status, secnum)
        else:
            self._put(path, self._render(tnum, cite, title_line, desc, body,
                                         credit, status, secnum), title_line, desc)
        self.stats["sections"] += 1
        self.stats[f"sections_t{tnum}"] += 1

    def _split(self, path, tnum, cite, title_line, desc, paras, credit, status, secnum):
        chunks, cur, n = [], [], 0
        for p in paras:
            if cur and n + len(p) > self.split_chars:
                chunks.append(cur); cur, n = [], 0
            cur.append(p); n += len(p) + 2
        if cur:
            chunks.append(cur)
        stem = path[:-3]
        links = []
        for i, c in enumerate(chunks, 1):
            pp = f"{stem}--part-{i}.md"
            pt = f"{title_line} (part {i} of {len(chunks)})"
            self._put(pp, self._render(tnum, cite, pt, c[0][:200], "\n\n".join(c),
                                       "", status, secnum, part=(i, len(chunks))), pt, c[0][:160])
            links.append((os.path.basename(pp), pt))
        parent = paras[0] + "\n\n# Parts\n\n" + "\n".join(f"- [{t}]({p})" for p, t in links)
        self._put(path, self._render(tnum, cite, title_line, desc, parent, credit,
                                     status, secnum, nparts=len(chunks)), title_line, desc)
        self.stats["split_sections"] += 1

    def _render(self, tnum, cite, title_line, desc, body, credit, status, secnum,
                part=None, nparts=None):
        okf_status = "deprecated" if status else "stable"  # any uslm status = not operative law
        res = (f"https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-"
               f"title{tnum}-section{secnum}&num=0&edition=prelim")
        pairs = [
            ("type", "Statute Section"),
            ("title", title_line),
            ("description", (desc[:240] + "…") if len(desc) > 240 else desc or cite),
            ("resource", res),
            ("tags", ["federal", "us-code", f"title-{tnum}"]),
            ("citation", cite),
            ("jurisdiction", "united-states"),
            ("jurisdiction_level", "federal"),
            ("status", okf_status),
            ("stale_after", self.stale_after),
            ("generated", {"by": TOOL_ACTOR, "at": self.run_at}),
            ("sources", self.src_raw()),
            ("checksum", hashlib.sha256(body.encode()).hexdigest()),
        ]
        if status:
            pairs.insert(9, ("uslm_status", status))
        if part:
            pairs.append(("part", f"{part[0]} of {part[1]}"))
        if nparts:
            pairs.append(("parts", str(nparts)))
        out = [emit_frontmatter(pairs), "# Text", "", body, ""]
        if credit and not part:
            out += ["# History", "", credit, ""]
        return "\n".join(out)

    def _put(self, path, content, title, desc):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        self.index[os.path.dirname(path)].append(
            (os.path.basename(path), title, normspace(desc)[:160]))
        self.stats["files"] += 1

    def set_dir_heading(self, d, h):
        self.dirmeta[d] = normspace(h)

    def write_indexes(self):
        alldirs = set(self.index)
        for d in list(alldirs):
            p = d
            while len(p) > len(self.out):
                alldirs.add(os.path.dirname(p)); p = os.path.dirname(p)
        alldirs.add(self.out)
        kids = defaultdict(set)
        for d in alldirs:
            if d != self.out:
                kids[os.path.dirname(d)].add(d)
        # root also lists non-federal dirs already on disk (e.g. california/)
        if os.path.isdir(self.out):
            for e in os.listdir(self.out):
                p = os.path.join(self.out, e)
                if os.path.isdir(p) and e != "references":
                    kids[self.out].add(p)
        for d in sorted(alldirs):
            lines = []
            if d == self.out:
                lines.append(f'---\nokf_version: "{OKF_VERSION}"\n---\n')
            h = self.dirmeta.get(d) or os.path.basename(d).replace("-", " ").title() or "Bundle"
            lines.append(f"# {h}\n")
            for s in sorted(kids.get(d, set())):
                sh = self.dirmeta.get(s) or os.path.basename(s).replace("-", " ").title()
                lines.append(f"* [{sh}]({os.path.basename(s)}/) - {sh}")
            if kids.get(d):
                lines.append("")
            for fname, title, desc in sorted(self.index.get(d, [])):
                lines.append(f"* [{title}]({fname})" + (f" - {desc}" if desc else ""))
            with open(os.path.join(d, "index.md"), "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            self.stats["indexes"] += 1

    def write_log(self, note):
        path = os.path.join(self.out, "log.md")
        entry = f"## {self.run_at[:10]}\n* **Update**: {note}\n"
        old = ""
        if os.path.exists(path):
            old = re.sub(r"^# [^\n]*\n+", "", open(path, encoding="utf-8").read(), count=1)
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Bundle Update Log\n\n" + entry + "\n" + old)

    def write_reports(self, expected):
        ref = os.path.join(self.out, "references")
        os.makedirs(ref, exist_ok=True)
        lines = [f"# Gaps report — federal/usc", "",
                 f"Run: {self.run_at} · dump: {self.dump_label}", ""]
        for t, exp in sorted(expected.items(), key=lambda kv: (len(kv[0]), kv[0])):
            got = self.stats.get(f"sections_t{t}", 0)
            lines.append(f"- Title {t}: wrote {got} / parsed {exp}"
                         + ("" if got >= exp else "  ⚠️ MISSING"))
        if self.gaps:
            lines += ["", "## Issues"] + [f"- {g}" for g in self.gaps[:2000]]
        with open(os.path.join(ref, "gaps-federal.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        with open(os.path.join(ref, "ingest-stats-federal.json"), "w", encoding="utf-8") as f:
            json.dump(dict(self.stats), f, indent=2, sort_keys=True)

# ---------------------------------------------------------------------------

def process_title(stream, tnum, w, expected, limit=0):
    """Stream one title's USLM XML; write a concept per <section>."""
    hier = []          # [(level, numvalue)] stack
    done = 0
    for ev, el in ET.iterparse(stream, events=("start", "end")):
        t = local(el.tag)
        if ev == "start":
            if t in LEVELS:
                hier.append([t, "", ""])   # level, num, heading (filled at end of children)
            continue
        # end events
        if t == "num" and hier and not hier[-1][1]:
            hier[-1][1] = el.get("value") or normspace(el.text or "")
        elif t == "heading" and hier and not hier[-1][2]:
            hier[-1][2] = normspace("".join(el.itertext()))
        elif t == "section":
            ident = el.get("identifier") or ""
            m = re.match(r"^/us/usc/t[0-9a-zA-Z]+/s(\S+)", ident)
            if m:
                secnum = m.group(1)  # ranged identifiers ("s221 .../s222") keep the first
                heading, paras, credit, status = section_text(el)
                hpairs = [(lv, num) for lv, num, _h in hier if num]
                w.write_section(tnum, hpairs, secnum, heading, paras, credit, status)
                # record directory headings
                d = os.path.join(w.out, "federal", "usc", f"title-{tnum}")
                for lv, num, h in hier:
                    if num:
                        d = os.path.join(d, f"{lv}-{slug(num)}")
                        if h:
                            w.set_dir_heading(d, f"{lv.title()} {num} — {h}" if h else f"{lv.title()} {num}")
                expected[tnum] += 1
                done += 1
                if limit and done >= limit:
                    el.clear()
                    return done
            el.clear()
        elif t in LEVELS:
            if hier and hier[-1][0] == t:
                hier.pop()
            el.clear()
    return done

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True, help="xml_uscAll@*.zip, a single title .xml/.zip, or a directory")
    ap.add_argument("--out", default="ok")
    ap.add_argument("--titles", default="", help="comma list, e.g. 18,26,5a (default: all)")
    ap.add_argument("--limit", type=int, default=0, help="sections per title (debug)")
    ap.add_argument("--split-chars", type=int, default=6000)
    ap.add_argument("--stale-after", default="")
    args = ap.parse_args()

    run_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale = args.stale_after or (dt.datetime.now(dt.timezone.utc) +
                                 dt.timedelta(days=90)).strftime("%Y-%m-%dT00:00:00Z")
    wanted = {t.strip().lower() for t in args.titles.split(",") if t.strip()}
    label = os.path.basename(args.dump.rstrip("/"))
    w = FederalWriter(args.out, run_at, label, stale, args.split_chars)
    expected = defaultdict(int)

    def title_num(name):
        m = re.match(r"usc(\d+[a-zA-Z]?)\.xml$", os.path.basename(name).lower())
        return m.group(1).lstrip("0") if m else None

    members = []
    if os.path.isdir(args.dump):
        for f in sorted(os.listdir(args.dump)):
            tn = title_num(f)
            if tn:
                members.append((tn, lambda f=f: open(os.path.join(args.dump, f), "rb")))
    elif args.dump.lower().endswith(".xml"):
        tn = title_num(args.dump) or "unknown"
        members.append((tn, lambda: open(args.dump, "rb")))
    else:
        zf = zipfile.ZipFile(args.dump)
        for n in sorted(zf.namelist()):
            tn = title_num(n)
            if tn:
                members.append((tn, lambda n=n: zf.open(n)))

    total = 0
    for tn, opener in members:
        if wanted and tn.lower() not in wanted:
            continue
        with opener() as f:
            n = process_title(f, tn.lower(), w, expected, args.limit)
        total += n
        print(f"  title {tn}: {n} sections", flush=True)

    w.set_dir_heading(args.out, "Open Knowledge: United States Law")
    w.set_dir_heading(os.path.join(args.out, "federal"), "Federal")
    w.set_dir_heading(os.path.join(args.out, "federal", "usc"), "United States Code")
    w.set_dir_heading(os.path.join(args.out, "california"), "California")
    for tn, _ in members:
        if wanted and tn.lower() not in wanted:
            continue
        w.set_dir_heading(os.path.join(args.out, "federal", "usc", f"title-{tn.lower()}"),
                          f"Title {tn.upper()}")
    w.write_indexes()
    w.write_log(f"Ingested {total} US Code sections "
                f"({','.join(sorted(wanted)) or 'all titles'}) from {label} via {TOOL_ACTOR}.")
    w.write_reports(expected)
    print(f"done: {w.stats['files']} files, {w.stats['indexes']} indexes, "
          f"{w.stats['empty_bodies']} empty, {len(w.gaps)} gaps", flush=True)

if __name__ == "__main__":
    main()

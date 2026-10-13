#!/usr/bin/env python3
"""
pubinfo_to_okf.py — California Legislative Information bulk dump -> Open Knowledge Format (OKF v0.2) bundle.

Converts the official CA "pubinfo" database dump (downloads.leginfo.legislature.ca.gov)
into an OKF knowledge bundle: one markdown concept file per code section, with YAML
frontmatter carrying citation / jurisdiction / provenance / lifecycle metadata, plus
generated index.md files per directory and a root log.md — per the OKF v0.2 spec
(github.com/GoogleCloudPlatform/open-knowledge-format).

Input: either the pubinfo_daily_*.zip / pubinfo_YYYY.zip itself (read in place, no
extraction needed) or a directory containing the extracted .dat/.lob files.

Tables used (column order confirmed against the official pubinfo_load.zip DDL):
  CODES_TBL.dat             code -> full code title ("PEN" -> "Penal Code")
  LAW_TOC_TBL.dat           hierarchy headings (division/title/part/chapter/article)
  LAW_TOC_SECTIONS_TBL.dat  expected-section inventory (used for validation)
  LAW_SECTION_TBL.dat       the sections; CONTENT_XML column names a .lob file (CAML XML)

.dat format: MySQL LOAD DATA — fields terminated by TAB, optionally enclosed by
backtick, lines terminated by \n, backslash escapes, literal NULL for nulls.

Usage:
  python3 pubinfo_to_okf.py --dump pubinfo_daily_Sat.zip --out ok --codes PEN
  python3 pubinfo_to_okf.py --dump ./extracted_dir --out ok            # all 29 codes
  python3 pubinfo_to_okf.py --dump dump.zip --list                     # inspect contents

No third-party dependencies (stdlib only).
"""

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict

TOOL_ACTOR = "pubinfo_to_okf/1.0"
OKF_VERSION = "0.2"

# Column orders from the official DDL (pubinfo_load.zip). Do not reorder.
COLS = {
    "codes_tbl": ["code", "title"],
    "law_toc_tbl": [
        "law_code", "division", "title", "part", "chapter", "article", "heading",
        "active_flg", "trans_uid", "trans_update", "node_sequence", "node_level",
        "node_position", "node_treepath", "contains_law_sections", "history_note",
        "op_statues", "op_chapter", "op_section",
    ],
    "law_toc_sections_tbl": [
        "id", "law_code", "node_treepath", "section_num", "section_order", "title",
        "op_statues", "op_chapter", "op_section", "trans_uid", "trans_update",
        "law_section_version_id", "seq_num",
    ],
    "law_section_tbl": [
        "id", "law_code", "section_num", "op_statues", "op_chapter", "op_section",
        "effective_date", "law_section_version_id", "division", "title", "part",
        "chapter", "article", "history", "content_xml", "active_flg",
        "trans_uid", "trans_update",
    ],
}

HIER_FIELDS = ["division", "title", "part", "chapter", "article"]

# ---------------------------------------------------------------------------
# Dump access: a directory or a zip, case-insensitive member lookup
# ---------------------------------------------------------------------------

class Dump:
    def __init__(self, path):
        self.path = path
        self.zf = None
        self._names = {}
        if os.path.isdir(path):
            for root, _dirs, files in os.walk(path):
                for f in files:
                    self._names[f.lower()] = os.path.join(root, f)
        else:
            self.zf = zipfile.ZipFile(path)
            for n in self.zf.namelist():
                self._names[os.path.basename(n).lower()] = n

    def names(self):
        return sorted(self._names)

    def has(self, name):
        return name.lower() in self._names

    def open(self, name):
        """Binary stream for a member, by basename, case-insensitive."""
        real = self._names.get(name.lower())
        if real is None:
            raise FileNotFoundError(name)
        if self.zf:
            return self.zf.open(real)
        return open(real, "rb")

    def read_text(self, name):
        with self.open(name) as f:
            return f.read().decode("utf-8", errors="replace")

# ---------------------------------------------------------------------------
# MySQL LOAD DATA .dat reader (tab fields, optional backtick enclosure,
# backslash escapes, newline-terminated records; enclosed fields may span lines)
# ---------------------------------------------------------------------------

ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", "`": "`"}

def iter_dat_rows(stream, columns, label=""):
    """Yield dicts per record. `stream` is a binary file object."""
    text = io.TextIOWrapper(stream, encoding="utf-8", errors="replace", newline="")
    fields, buf = [], []
    enclosed = False   # currently inside a backtick-enclosed field
    at_start = True    # at the start of a field (enclosure only recognized here)
    esc = False
    ncols = len(columns)

    def finish_field():
        nonlocal buf, at_start, enclosed
        val = "".join(buf)
        buf = []
        at_start, enclosed = True, False
        fields.append(None if val == "NULL" else val)

    while True:
        chunk = text.read(65536)
        if not chunk:
            break
        for ch in chunk:
            if esc:
                buf.append(ESCAPES.get(ch, ch))
                esc = False
                at_start = False
                continue
            if ch == "\\":
                esc = True
                continue
            if enclosed:
                if ch == "`":
                    enclosed = False  # closing quote; next must be \t or \n
                else:
                    buf.append(ch)
                continue
            if ch == "`" and at_start:
                enclosed = True
                at_start = False
                continue
            if ch == "\t":
                finish_field()
                continue
            if ch == "\n":
                # tolerate \r\n
                if buf and buf[-1] == "\r":
                    buf.pop()
                finish_field()
                if len(fields) == ncols:
                    yield dict(zip(columns, fields))
                elif any(f not in (None, "") for f in fields):
                    sys.stderr.write(
                        f"[warn] {label}: skipped record with {len(fields)} fields "
                        f"(expected {ncols}): {str(fields)[:160]}\n")
                fields = []
                continue
            at_start = False
            buf.append(ch)
    # trailing record without final newline
    if buf or fields:
        finish_field()
        if len(fields) == ncols:
            yield dict(zip(columns, fields))

# ---------------------------------------------------------------------------
# CAML / XML -> plain markdown-ish text
# ---------------------------------------------------------------------------

BLOCK_TAGS = {"p", "para", "content", "history", "preamble", "div", "li",
              "item", "tr", "row", "caption", "title", "heading", "br"}

def local(tag):
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""

def xml_to_paragraphs(xml_text):
    """Convert a CAML lob into a list of text paragraphs. Falls back to tag
    stripping if the XML does not parse."""
    xml_text = xml_text.strip()
    if not xml_text:
        return []
    paras = []
    try:
        # strip any leading junk before the first '<'
        start = xml_text.find("<")
        root = ET.fromstring(xml_text[start:] if start > 0 else xml_text)
    except ET.ParseError:
        # fallback: naive tag strip, paragraph breaks on </p>
        t = re.sub(r"(?i)</p\s*>", "\n\n", xml_text)
        t = re.sub(r"<[^>]+>", " ", t)
        return [normspace(p) for p in t.split("\n\n") if normspace(p)]

    cur = []

    def flush():
        txt = normspace("".join(cur))
        cur.clear()
        if txt:
            paras.append(txt)

    def walk(el):
        tag = local(el.tag)
        is_block = tag in BLOCK_TAGS
        if is_block:
            flush()
        if el.text:
            cur.append(el.text)
        for child in el:
            walk(child)
            if child.tail:
                cur.append(child.tail)
        if is_block:
            flush()

    walk(root)
    flush()
    # drop a leading paragraph that is just the section number ("502.")
    if paras and re.fullmatch(r"[0-9][0-9a-zA-Z.\- ]{0,20}\.?", paras[0]):
        paras = paras[1:]
    return paras

def normspace(s):
    return re.sub(r"[ \t\r\f\v]+", " ", s.replace(" ", " ")).strip()

# ---------------------------------------------------------------------------
# YAML frontmatter emitter (hand-rolled, deterministic, safe quoting)
# ---------------------------------------------------------------------------

def yq(s):
    """Double-quoted YAML scalar."""
    s = str(s)
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", s).replace("\n", " ")
    return f'"{s}"'

def emit_frontmatter(pairs):
    """pairs: list of (key, value) where value is str | list[str] | dict | raw tuple."""
    out = ["---"]
    for k, v in pairs:
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, list):
            out.append(f"{k}: [{', '.join(yq(x) for x in v)}]")
        elif isinstance(v, dict):
            inner = ", ".join(f"{ik}: {yq(iv)}" for ik, iv in v.items())
            out.append(f"{k}: {{ {inner} }}")
        elif isinstance(v, tuple) and v[0] == "raw":
            out.append(f"{k}: {v[1]}")
        else:
            out.append(f"{k}: {yq(v)}")
    out.append("---")
    return "\n".join(out) + "\n"

# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------

def slug(s):
    s = (s or "").strip().rstrip(".")
    s = s.lower().replace(".", "-")
    s = re.sub(r"[^a-z0-9\-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "x"

def hier_dirs(row):
    parts = []
    for f in HIER_FIELDS:
        v = (row.get(f) or "").strip()
        if v:
            parts.append(f"{f}-{slug(v)}")
    return parts

def clean_code_title(title):
    """Dump titles look like 'Penal Code - PEN'; drop the trailing abbreviation."""
    return re.sub(r"\s*-\s*[A-Z]{2,6}\s*$", "", normspace(title or ""))

def cite_name(code_title):
    """'Penal Code' -> 'Cal. Penal Code'; 'Vehicle Code' -> 'Cal. Vehicle Code'."""
    t = clean_code_title(code_title)
    return f"Cal. {t}" if t else "Cal. Code"

def last_amended_year(history):
    years = re.findall(r"Stats\.?\s*(\d{4})", history or "")
    years += re.findall(r"\b(19\d\d|20\d\d)\b", history or "")
    return max(years) if years else None

def leginfo_url(law_code, section_num):
    num = (section_num or "").strip()
    if not num.endswith("."):
        num += "."
    return (f"https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml"
            f"?lawCode={law_code}&sectionNum={num}")

PRACTICE_TAGS = {
    "PEN": "criminal-law", "VEH": "traffic", "CIV": "civil-law", "CCP": "civil-procedure",
    "FAM": "family-law", "LAB": "labor-employment", "BPC": "business-professions",
    "HSC": "health-safety", "RTC": "tax", "GOV": "government", "EDC": "education",
    "CORP": "corporations", "COM": "commercial-law", "INS": "insurance", "EVID": "evidence",
    "PROB": "probate", "WIC": "welfare-institutions", "UIC": "unemployment-insurance",
    "FIN": "financial-institutions", "ELEC": "elections", "FGC": "fish-game",
    "FAC": "food-agriculture", "HNC": "harbors-navigation", "MVC": "military-veterans",
    "PCC": "public-contracts", "PRC": "public-resources", "PUC": "public-utilities",
    "SHC": "streets-highways", "WAT": "water",
}

# ---------------------------------------------------------------------------
# Concept writer
# ---------------------------------------------------------------------------

class BundleWriter:
    def __init__(self, out_root, run_at, dump_label, dump_mtime, stale_after, split_chars):
        self.out = out_root
        self.run_at = run_at
        self.dump_label = dump_label
        self.dump_mtime = dump_mtime
        self.stale_after = stale_after
        self.split_chars = split_chars
        self.index = defaultdict(list)    # dirpath -> [(filename, title, description)]
        self.dirmeta = {}                 # dirpath -> heading
        self.stats = defaultdict(int)
        self.gaps = []
        self.seen_paths = set()

    def concept_path(self, code, row):
        d = os.path.join(self.out, "california", "codes", code.lower(), *hier_dirs(row))
        fname = f"sec-{slug(row['section_num'])}.md"
        p = os.path.join(d, fname)
        if p in self.seen_paths:  # CA genuinely has duplicate section numbers
            for i in range(2, 50):
                alt = os.path.join(d, f"sec-{slug(row['section_num'])}--v{i}.md")
                if alt not in self.seen_paths:
                    self.gaps.append(f"duplicate section number: {code} § {row['section_num']} -> {alt}")
                    p = alt
                    break
        self.seen_paths.add(p)
        return p

    def write_section(self, code, code_title, row, paragraphs):
        path = self.concept_path(code, row)
        body_text = "\n\n".join(paragraphs)
        base_title = f"{cite_name(code_title)} § {row['section_num'].rstrip('.')}"
        desc = (paragraphs[0][:240] + ("…" if len(paragraphs[0]) > 240 else "")) if paragraphs else ""
        if not paragraphs:
            self.gaps.append(f"empty body: {base_title} ({row.get('content_xml')})")
            self.stats["empty_bodies"] += 1

        if len(body_text) > self.split_chars and len(paragraphs) > 1:
            self._write_split(code, code_title, row, paragraphs, path, base_title, desc)
        else:
            md = self._render(code, code_title, row, base_title, desc,
                              body_text, part=None)
            self._put(path, md, base_title, desc)
        self.stats["sections"] += 1
        self.stats[f"sections_{code}"] += 1

    def _write_split(self, code, code_title, row, paragraphs, path, base_title, desc):
        chunks, cur, n = [], [], 0
        for p in paragraphs:
            if cur and n + len(p) > self.split_chars:
                chunks.append(cur)
                cur, n = [], 0
            cur.append(p)
            n += len(p) + 2
        if cur:
            chunks.append(cur)
        stem = path[:-3]
        links = []
        for i, chunk in enumerate(chunks, 1):
            ppath = f"{stem}--part-{i}.md"
            ptitle = f"{base_title} (part {i} of {len(chunks)})"
            md = self._render(code, code_title, row, ptitle,
                              chunk[0][:240], "\n\n".join(chunk),
                              part=(i, len(chunks)))
            self._put(ppath, md, ptitle, chunk[0][:160])
            links.append((os.path.basename(ppath), ptitle))
        # parent concept: summary + links to parts
        rel = [f"- [{t}]({os.path.basename(p)})" for p, t in links]
        parent_body = (paragraphs[0] + "\n\n# Parts\n\n" +
                       "\n".join(rel))
        md = self._render(code, code_title, row, base_title, desc, parent_body,
                          part=None, is_parent=True, nparts=len(chunks))
        self._put(path, md, base_title, desc)
        self.stats["split_sections"] += 1

    def _render(self, code, code_title, row, title, desc, body_text,
                part=None, is_parent=False, nparts=None):
        history = normspace(row.get("history") or "")
        la = last_amended_year(history)
        tags = ["california", slug(clean_code_title(code_title) or code)]
        pt = PRACTICE_TAGS.get(code)
        if pt:
            tags.append(pt)
        status = "stable" if (row.get("active_flg") or "Y").upper() == "Y" else "deprecated"
        pairs = [
            ("type", "Statute Section"),
            ("title", title),
            ("description", desc or f"{title}."),
            ("resource", leginfo_url(code, row["section_num"])),
            ("tags", tags),
            ("citation", f"{cite_name(code_title)} § {row['section_num'].rstrip('.')}"),
            ("jurisdiction", "california"),
            ("jurisdiction_level", "state"),
            ("law_code", code),
            ("effective_date", normspace(row.get("effective_date") or "").split(" ")[0]),
            ("last_amended", la),
            ("status", status),
            ("stale_after", self.stale_after),
            ("generated", {"by": TOOL_ACTOR, "at": self.run_at}),
            ("sources", ("raw",
                "\n  - { id: pubinfo, resource: " + yq("https://downloads.leginfo.legislature.ca.gov/" + self.dump_label) +
                ", title: " + yq("CA Legislative Information bulk database (" + self.dump_label + ")") +
                ", author: " + yq("process:ca-legislature") +
                (", last_modified: " + yq(self.dump_mtime) if self.dump_mtime else "") + " }")),
            ("checksum", hashlib.sha256(body_text.encode("utf-8")).hexdigest()),
        ]
        if part:
            pairs.append(("part", f"{part[0]} of {part[1]}"))
        if is_parent and nparts:
            pairs.append(("parts", str(nparts)))
        fm = emit_frontmatter(pairs)
        out = [fm, "# Text", "", body_text if not is_parent else body_text, ""]
        if history and not part:
            out += ["# History", "", history, ""]
        return "\n".join(out)

    def _put(self, path, content, title, desc):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        self.index[os.path.dirname(path)].append(
            (os.path.basename(path), title, normspace(desc or "")[:160]))
        self.stats["files"] += 1

    # ---- indexes, log, reports ------------------------------------------

    def set_dir_heading(self, dirpath, heading):
        self.dirmeta[dirpath] = normspace(heading)

    def write_indexes(self):
        # ensure every ancestor dir of content dirs gets an index listing its children
        alldirs = set(self.index)
        for d in list(alldirs):
            p = d
            while len(p) > len(self.out):
                parent = os.path.dirname(p)
                alldirs.add(parent)
                p = parent
        alldirs.add(self.out)
        children = defaultdict(set)
        for d in alldirs:
            if d != self.out:
                children[os.path.dirname(d)].add(d)

        for d in sorted(alldirs):
            lines = []
            if d == self.out:
                lines.append(f'---\nokf_version: "{OKF_VERSION}"\n---\n')
            heading = self.dirmeta.get(d) or os.path.basename(d).replace("-", " ").title() or "Bundle"
            lines.append(f"# {heading}\n")
            subs = sorted(children.get(d, set()))
            if subs:
                for s in subs:
                    h = self.dirmeta.get(s) or os.path.basename(s).replace("-", " ").title()
                    lines.append(f"* [{h}]({os.path.basename(s)}/) - {h}")
                lines.append("")
            for fname, title, desc in sorted(self.index.get(d, [])):
                lines.append(f"* [{title}]({fname})" + (f" - {desc}" if desc else ""))
            with open(os.path.join(d, "index.md"), "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            self.stats["indexes"] += 1

    def write_log(self, note):
        path = os.path.join(self.out, "log.md")
        today = self.run_at[:10]
        entry = f"## {today}\n* **Update**: {note}\n"
        old = ""
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                old = f.read()
            old = re.sub(r"^# [^\n]*\n+", "", old, count=1)
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Bundle Update Log\n\n" + entry + "\n" + old)

    def write_reports(self, expected_counts):
        refdir = os.path.join(self.out, "references")
        os.makedirs(refdir, exist_ok=True)
        lines = ["# Gaps report", "",
                 f"Run: {self.run_at} · dump: {self.dump_label}", ""]
        for code, exp in sorted(expected_counts.items()):
            got = self.stats.get(f"sections_{code}", 0)
            flag = "" if got >= exp else "  ⚠️ MISSING"
            lines.append(f"- {code}: wrote {got} / TOC expects {exp}{flag}")
        lines.append("")
        if self.gaps:
            lines.append("## Issues")
            lines += [f"- {g}" for g in self.gaps[:2000]]
            if len(self.gaps) > 2000:
                lines.append(f"- … and {len(self.gaps) - 2000} more")
        with open(os.path.join(refdir, "gaps.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        with open(os.path.join(refdir, "ingest-stats.json"), "w", encoding="utf-8") as f:
            json.dump(dict(self.stats), f, indent=2, sort_keys=True)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--dump", required=True, help="pubinfo zip file or extracted directory")
    ap.add_argument("--out", default="ok", help="bundle output root (default: ok)")
    ap.add_argument("--codes", default="", help="comma-separated law codes (e.g. PEN,VEH); default all")
    ap.add_argument("--limit", type=int, default=0, help="stop after N sections (debug)")
    ap.add_argument("--split-chars", type=int, default=6000,
                    help="split sections whose body exceeds this many chars (~1500 tokens)")
    ap.add_argument("--stale-after", default="",
                    help="ISO datetime; default = Jan 2 of next year (next CA effective date)")
    ap.add_argument("--list", action="store_true", help="list dump members and exit")
    args = ap.parse_args()

    dump = Dump(args.dump)
    if args.list:
        names = dump.names()
        for n in names[:60]:
            print(n)
        print(f"... {len(names)} members total")
        return

    wanted = {c.strip().upper() for c in args.codes.split(",") if c.strip()}
    run_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now = dt.datetime.now(dt.timezone.utc)
    stale = args.stale_after or f"{now.year + 1}-01-02T00:00:00Z"
    dump_label = os.path.basename(args.dump.rstrip("/"))
    dump_mtime = dt.datetime.fromtimestamp(os.path.getmtime(args.dump),
                                           dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    w = BundleWriter(args.out, run_at, dump_label, dump_mtime, stale, args.split_chars)

    # 1) code titles
    code_titles = {}
    with dump.open("CODES_TBL.dat") as f:
        for r in iter_dat_rows(f, COLS["codes_tbl"], "codes_tbl"):
            code_titles[(r["code"] or "").strip()] = normspace(r["title"] or "")
    print(f"[1/5] codes_tbl: {len(code_titles)} codes", flush=True)

    # 2) TOC headings -> directory metadata
    heads = 0
    with dump.open("LAW_TOC_TBL.dat") as f:
        for r in iter_dat_rows(f, COLS["law_toc_tbl"], "law_toc_tbl"):
            code = (r["law_code"] or "").strip()
            if wanted and code not in wanted:
                continue
            if (r.get("active_flg") or "Y").upper() != "Y":
                continue
            d = os.path.join(args.out, "california", "codes", code.lower(), *hier_dirs(r))
            if r.get("heading"):
                w.set_dir_heading(d, r["heading"])
                heads += 1
    print(f"[2/5] law_toc_tbl: {heads} active headings", flush=True)

    # 3) expected section counts, for validation
    expected = defaultdict(int)
    with dump.open("LAW_TOC_SECTIONS_TBL.dat") as f:
        for r in iter_dat_rows(f, COLS["law_toc_sections_tbl"], "law_toc_sections_tbl"):
            code = (r["law_code"] or "").strip()
            if wanted and code not in wanted:
                continue
            expected[code] += 1
    print(f"[3/5] law_toc_sections_tbl: {sum(expected.values())} expected sections", flush=True)

    # 4) the sections themselves
    done = 0
    with dump.open("LAW_SECTION_TBL.dat") as f:
        for r in iter_dat_rows(f, COLS["law_section_tbl"], "law_section_tbl"):
            code = (r["law_code"] or "").strip()
            if wanted and code not in wanted:
                continue
            if (r.get("active_flg") or "Y").upper() != "Y":
                continue
            lob = (r.get("content_xml") or "").strip()
            paragraphs = []
            if lob:
                lobname = os.path.basename(lob.replace("\\", "/"))
                try:
                    paragraphs = xml_to_paragraphs(dump.read_text(lobname))
                except FileNotFoundError:
                    w.gaps.append(f"missing lob: {lobname} for {code} § {r['section_num']}")
            w.write_section(code, code_titles.get(code, code), r, paragraphs)
            done += 1
            if done % 5000 == 0:
                print(f"    … {done} sections", flush=True)
            if args.limit and done >= args.limit:
                break
    print(f"[4/5] law_section_tbl: {done} active sections written", flush=True)

    # 5) indexes, headings for fixed dirs, log, reports
    w.set_dir_heading(args.out, "Open Knowledge: United States Law")
    w.set_dir_heading(os.path.join(args.out, "california"), "California")
    w.set_dir_heading(os.path.join(args.out, "california", "codes"), "California Codes")
    for code, t in code_titles.items():
        if wanted and code not in wanted:
            continue
        w.set_dir_heading(os.path.join(args.out, "california", "codes", code.lower()),
                          clean_code_title(t) or code)
    w.write_indexes()
    codes_note = ",".join(sorted(wanted)) if wanted else "all codes"
    w.write_log(f"Ingested {done} active sections ({codes_note}) from {dump_label} "
                f"via {TOOL_ACTOR}.")
    w.write_reports(expected)
    print(f"[5/5] bundle at {args.out}: {w.stats['files']} concept files, "
          f"{w.stats['indexes']} indexes, {w.stats['empty_bodies']} empty bodies, "
          f"{len(w.gaps)} gap entries", flush=True)

if __name__ == "__main__":
    main()

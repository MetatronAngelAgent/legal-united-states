#!/usr/bin/env python3
"""
uslm_rules_to_okf.py — Federal court rules from the official USC appendix titles
(OLRC USLM XML release) -> OKF v0.2 concepts, one file per rule.

  28a  courtRules/App   -> Federal Rules of Appellate Procedure   (Fed. R. App. P.)
  28a  courtRules/Civil -> Federal Rules of Civil Procedure       (Fed. R. Civ. P.)
  28a  courtRules/Evid  -> Federal Rules of Evidence              (Fed. R. Evid.)
  18a  courtRules/Crim  -> Federal Rules of Criminal Procedure    (Fed. R. Crim. P.)
  11a  (courtRule)      -> Federal Rules of Bankruptcy Procedure  (Fed. R. Bankr. P.)

Output: <out>/federal/court-rules/<set>/rule-<n>.md (+ index.md per set), reusing the
text renderer and frontmatter conventions of uslm_to_okf.py (must sit next to it).
"""
import argparse, datetime as dt, hashlib, os, re, sys, zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uslm_to_okf as U

TOOL = "uslm_rules_to_okf/1.0"
SETS = {
    "App":   ("frap",  "Federal Rules of Appellate Procedure", "Fed. R. App. P."),
    "Civil": ("frcp",  "Federal Rules of Civil Procedure",     "Fed. R. Civ. P."),
    "Evid":  ("fre",   "Federal Rules of Evidence",            "Fed. R. Evid."),
    "Crim":  ("frcrp", "Federal Rules of Criminal Procedure",  "Fed. R. Crim. P."),
    "Bankr": ("frbp",  "Federal Rules of Bankruptcy Procedure", "Fed. R. Bankr. P."),
}
TITLE_FILES = {"usc28a.xml": None, "usc18a.xml": None, "usc11a.xml": "Bankr"}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True, help="xml_uscAll@*.zip")
    ap.add_argument("--out", default="legal")
    a = ap.parse_args()
    run_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=90)).strftime("%Y-%m-%dT00:00:00Z")
    label = os.path.basename(a.dump)
    zf = zipfile.ZipFile(a.dump)
    names = {os.path.basename(n).lower(): n for n in zf.namelist()}
    counts, index = defaultdict(int), defaultdict(list)

    for fname, forced in TITLE_FILES.items():
        root = ET.fromstring(zf.read(names[fname]))
        parent = {c: p for p in root.iter() for c in p}
        for el in root.iter():
            if U.local(el.tag) != "courtrule":
                continue
            code = forced
            if not code:
                m = re.search(r"/courtRules/([^/]+)/", el.get("identifier") or "")
                p = el
                while not m and p in parent:
                    p = parent[p]
                    m = re.search(r"/courtRules/([^/]+)", p.get("identifier") or "")
                code = m.group(1) if m else None
            if code not in SETS:
                continue
            slugset, setname, cite_prefix = SETS[code]
            num_el = next((c for c in el if U.local(c.tag) == "num"), None)
            num = (num_el.get("value") if num_el is not None else "") or \
                  re.sub(r"^Rule\s+|\.$", "", U.normspace("".join(num_el.itertext())) if num_el is not None else "")
            heading, paras, credit, status = U.section_text(el)
            if not num:
                continue
            body = "\n\n".join(paras)
            cite = f"{cite_prefix} {num}"
            title = f"{cite} — {heading}" if heading else cite
            okf_status = "deprecated" if (status or re.search(r"\b(Abrogated|Repealed|Reserved)\b", heading or "", re.I)) else "stable"
            path = os.path.join(a.out, "federal", "court-rules", slugset, f"rule-{U.slug(num)}.md")
            pairs = [
                ("type", "Court Rule"),
                ("title", title),
                ("description", heading or cite),
                ("resource", f"https://uscode.house.gov/ (USC Title {fname[3:-4].lstrip('0').upper()} Appendix, {setname})"),
                ("tags", ["federal", "court-rules", slugset]),
                ("citation", cite),
                ("rule_set", setname),
                ("jurisdiction", "united-states"),
                ("jurisdiction_level", "federal"),
                ("status", okf_status),
                ("stale_after", stale),
                ("generated", {"by": TOOL, "at": run_at}),
                ("sources", ("raw", "\n  - { id: olrc-releasepoint, resource: " +
                             U.yq("https://uscode.house.gov/download/releasepoints/ (" + label + ")") +
                             ", title: " + U.yq(f"US Code appendix ({setname}), USLM XML") +
                             ", author: " + U.yq("process:us-house-olrc") + " }")),
                ("checksum", hashlib.sha256(body.encode()).hexdigest()),
            ]
            md = U.emit_frontmatter(pairs) + "\n# Text\n\n" + body + "\n"
            if credit:
                md += "\n# History\n\n" + credit + "\n"
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if os.path.exists(path) and counts[(slugset, num)]:
                path = path[:-3] + f"--v{counts[(slugset, num)] + 1}.md"
            open(path, "w", encoding="utf-8").write(md)
            counts[(slugset, num)] += 1
            counts[slugset] += 1
            index[slugset].append((os.path.basename(path), title, num))

    base = os.path.join(a.out, "federal", "court-rules")
    def numkey(n):
        return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", n)]
    lines_root = ["# Federal Court Rules", ""]
    for code, (slugset, setname, _) in SETS.items():
        items = sorted(index.get(slugset, []), key=lambda t: numkey(t[2]))
        if not items:
            continue
        lines_root.append(f"* [{setname}]({slugset}/) - {len(items)} rules")
        with open(os.path.join(base, slugset, "index.md"), "w", encoding="utf-8") as f:
            f.write(f"# {setname}\n\n" + "\n".join(f"* [{t}]({fn})" for fn, t, _ in items) + "\n")
        print(f"{setname}: {len(items)} rules")
    open(os.path.join(base, "index.md"), "w", encoding="utf-8").write("\n".join(lines_root) + "\n")

if __name__ == "__main__":
    main()

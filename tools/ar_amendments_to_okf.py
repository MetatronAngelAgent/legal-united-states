#!/usr/bin/env python3
"""
ar_amendments_to_okf.py — flag Arkansas Code sections changed after the public-domain snapshot.

The `gap-states` layer holds the Arkansas Code as of Release 78 (2020-11-06). The Arkansas
Legislature publishes, for every session, a "Code Sections Amended" table: each Arkansas Code
section an Act amended, repealed (RP), created (NS) or renumbered (RN), with the Act number
(https://arkleg.state.ar.us/Acts/CodeSectionsAmended). robots.txt allows this (it only blocks
ChatGPT agents). This script reads those tables for every session after the snapshot and:

  * adds `amended_after_snapshot` + tag `amended-since-snapshot` to each affected section, and a
    "# Amendments since this snapshot" list linking the official Act PDFs;
  * marks sections repealed since the snapshot (`repealed_after_snapshot`);
  * writes arkansas/statutes/changes-since-snapshot.md listing every new section (NS) and change.

It does not rewrite statutory text — it tells a reader (or an agent) exactly where the snapshot
is out of date and which official Act to read.
Usage:  python3 ar_amendments_to_okf.py --cache ar_cache --out legal
"""
import argparse, html, json, os, re, sys, datetime as dt
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import regs_common as R
import uslm_to_okf as U

BASE = "https://arkleg.state.ar.us"
SESSIONS = ["2021/2021R", "2021/2021S1", "2021/2021S2", "2021/2022F", "2021/2022S3", "2023/2023R", "2023/2023S1",
            "2023/2024F", "2023/2024S2", "2025/2025R", "2025/2026F", "2025/2026S1"]
SNAPSHOT = "2020-11-06"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", default="legal")
    a = ap.parse_args()
    root = os.path.join(a.out, "arkansas", "statutes")
    if not os.path.isdir(root):
        raise SystemExit("run the gap-states layer first")
    # discover sessions offered now (keeps working as new sessions appear)
    sp = R.fetch(BASE + "/Acts/SearchByRange", os.path.join(a.cache, "sessions.html"), delay=0)
    offered = re.findall(r'option value="(\d{4}/\d{4}[A-Z]\d?)"', open(sp, encoding="utf-8", errors="replace").read()) if sp else []
    sessions = sorted({s for s in offered + SESSIONS if s.split("/")[1][:4] >= "2021"})
    changes = defaultdict(list)          # base section -> [(session, act, flags, cite, measure)]
    for ses in sessions:
        enc = ses.replace("/", "%2F")
        for t in [str(i) for i in range(0, 30)] + ["Constitution"]:
            fp = R.fetch(f"{BASE}/Acts/CodeSection?section={t}&ddBienniumSession={enc}",
                         os.path.join(a.cache, ses.replace("/", "_"), f"title-{t}.html"), delay=1.5)
            if not fp:
                continue
            txt = html.unescape(re.sub(r"<[^>]+>", "|", open(fp, encoding="utf-8", errors="replace").read()))
            cells = [c.strip() for c in re.split(r"\|+", txt) if c.strip()]
            i = 0
            while i < len(cells):
                if cells[i] == "Arkansas Code Number:" and i + 2 < len(cells):
                    k = i + 2 if cells[i + 1] == "Arkansas Code Number:" else i + 1
                    rec = {"cite": cells[k]}
                    j = k + 1
                    while j < len(cells) and cells[j] != "Arkansas Code Number:":
                        for key, lab in (("act", "Act Number:"), ("ec", "Emergency Clause:"), ("rp", "Repealed:"),
                                         ("ns", "New Section:"), ("rn", "Renumbered:"), ("measure", "Measure:")):
                            if cells[j] == lab and j + 1 < len(cells):
                                rec[key] = cells[j + 1]
                        j += 1
                    base = re.match(r"^(\d+-\d+(?:\.\d+)?-\d+(?:\.\d+)?)", rec["cite"])
                    if base and rec.get("act"):
                        changes[base.group(1)].append((ses, rec["act"], rec))
                    i = j
                else:
                    i += 1
        print(f"{ses}: {sum(1 for v in changes.values() for s, *_ in v if s == ses)} section changes", flush=True)
    files = {}
    for dp, _, fs in os.walk(root):
        for f in fs:
            m = re.match(r"^sec-(.+?)(?:-v\d+)?\.md$", f)
            if m and "--" not in f:
                files.setdefault(m.group(1), []).append(os.path.join(dp, f))
    patched, new_secs, repealed = 0, [], 0
    for sec, recs in changes.items():
        paths = files.get(U.slug(sec))
        acts = sorted({(s, act) for s, act, _ in recs})
        if not paths:
            if any(r.get("ns") == "Yes" for _, _, r in recs):
                new_secs.append((sec, recs))
            continue
        lines, rp = [], False
        for s, act, r in sorted(recs, key=lambda x: (x[0], int(re.sub(r"\D", "", x[1]) or 0))):
            flags = [k.upper() for k in ("rp", "ns", "rn", "ec") if r.get(k) == "Yes"]
            rp = rp or r.get("rp") == "Yes" and r["cite"] == sec
            url = f"{BASE}/Home/FTPDocument?path=%2FACTS%2F{s.split('/')[1]}%2FPublic%2FACT{r['act']}.pdf"
            lines.append(f"- [Act {r['act']} ({s.split('/')[1]}, {r.get('measure', '')})]({url}) — § {r['cite']}" +
                         (f" [{', '.join(flags)}]" if flags else ""))
        for p in paths:
            t = open(p, encoding="utf-8").read()
            if "amended_after_snapshot:" in t:
                t = re.sub(r"\n# Amendments since this snapshot\n.*\Z", "\n", t, flags=re.S)
                t = re.sub(r'^amended_after_snapshot: .*\n|^repealed_after_snapshot: .*\n', "", t, flags=re.M)
            fm_end = t.index("\n---\n", 4)
            add = "amended_after_snapshot: [" + ", ".join(U.yq("Act %s of %s" % (act, s.split("/")[1])) for s, act in acts) + "]\n"
            if rp:
                add += 'repealed_after_snapshot: "true"\n'
            fm = t[:fm_end + 1]
            fm = fm.replace('"historical-snapshot"]', '"historical-snapshot", "amended-since-snapshot"]', 1)
            t = fm + add + t[fm_end + 1:].rstrip("\n") + "\n\n# Amendments since this snapshot\n\n" + \
                f"This section was changed after {SNAPSHOT}; the text above is out of date. Official Acts:\n\n" + "\n".join(lines) + "\n"
            open(p, "w", encoding="utf-8").write(t)
            patched += 1
        repealed += 1 if rp else 0
    rep = [f"# Arkansas Code — changes since the {SNAPSHOT} snapshot\n",
           f"From the Arkansas Legislature's Code Sections Amended tables, sessions {sessions[0]} to {sessions[-1]}. "
           f"{len(changes):,} code sections changed; {patched:,} section files flagged; {len(new_secs):,} new sections not in the snapshot.\n",
           "## New sections enacted since the snapshot\n"]
    for sec, recs in sorted(new_secs, key=lambda x: [int(n) if n.isdigit() else n for n in re.split(r"(\d+)", x[0])]):
        s, act, r = recs[0]
        rep.append(f"- § {sec} — [Act {act} of {s.split('/')[1]}]({BASE}/Home/FTPDocument?path=%2FACTS%2F{s.split('/')[1]}%2FPublic%2FACT{act}.pdf)")
    open(os.path.join(root, "changes-since-snapshot.md"), "w", encoding="utf-8").write(
        '---\ntype: "Report"\ntitle: "Arkansas Code — changes since the 2020 snapshot"\n'
        'description: "Every Arkansas Code section amended, repealed or created by Acts after 2020-11-06, with links to the official Acts."\n'
        f'tags: ["arkansas", "statutes", "amendments"]\ngenerated: {{ by: "ar_amendments_to_okf/1.0", at: "{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}" }}\n---\n\n' +
        "\n".join(rep) + "\n")
    idx = os.path.join(root, "index.md")
    s = open(idx, encoding="utf-8").read()
    if "changes-since-snapshot.md" not in s:
        open(idx, "w", encoding="utf-8").write(s.replace("\n\n* ", "\n\n* [Changes since the snapshot (Acts 2021–present)](changes-since-snapshot.md)\n* ", 1))
    ref = os.path.join(a.out, "references"); os.makedirs(ref, exist_ok=True)
    json.dump({"sessions": sessions, "sections_changed": len(changes), "files_flagged": patched,
               "repealed_since": repealed, "new_sections": len(new_secs)},
              open(os.path.join(ref, "ingest-stats-ar-amendments.json"), "w"), indent=1)
    print(f"flagged {patched} files; {len(changes)} sections changed; {len(new_secs)} new; {repealed} repealed", flush=True)

if __name__ == "__main__":
    main()

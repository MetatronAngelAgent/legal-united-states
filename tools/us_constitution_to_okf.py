#!/usr/bin/env python3
"""
us_constitution_to_okf.py — U.S. Constitution (National Archives transcription) -> OKF.

Sources (public domain, NARA):
  https://www.archives.gov/founding-docs/constitution-transcript   (Preamble, Articles I–VII)
  https://www.archives.gov/founding-docs/bill-of-rights-transcript (Amendments I–X, ratified text only)
  https://www.archives.gov/founding-docs/amendments-11-27          (Amendments XI–XXVII)

Output: <out>/federal/constitution/{preamble.md, article-N/sec-N.md, article-N.md, amendment-N.md}
Note: this is the original-text transcription; superseded clauses are NOT removed —
the amendment that modified them says so in its notes (e.g. Amendment XIV, § 2).
"""
import argparse, datetime as dt, hashlib, html, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uslm_to_okf as U

TOOL = "us_constitution_to_okf/1.0"
NARA = "https://www.archives.gov/founding-docs/"
ROMAN = {v: i for i, v in enumerate(
    "I II III IV V VI VII VIII IX X XI XII XIII XIV XV XVI XVII XVIII XIX XX XXI XXII XXIII XXIV XXV XXVI XXVII".split(), 1)}

def lines_of(path):
    h = open(path, encoding="utf-8", errors="replace").read()
    t = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", "", h)
    t = re.sub(r"<(br|/p|/h\d|/div|/li)[^>]*>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    return [l.strip() for l in t.splitlines() if l.strip()]

def write(out, rel, title, cite, desc, body, url, run_at, notes=None, typ="Constitutional Provision"):
    path = os.path.join(out, "federal", "constitution", rel)
    pairs = [("type", typ), ("title", title), ("description", desc), ("resource", url),
             ("tags", ["federal", "constitution"]), ("citation", cite),
             ("jurisdiction", "united-states"), ("jurisdiction_level", "federal"),
             ("status", "stable"), ("generated", {"by": TOOL, "at": run_at}),
             ("sources", ("raw", "\n  - { id: nara, resource: " + U.yq(url) +
                          ", title: " + U.yq("National Archives transcription") +
                          ", author: " + U.yq("process:us-national-archives") + " }")),
             ("checksum", hashlib.sha256(body.encode()).hexdigest())]
    md = U.emit_frontmatter(pairs) + "\n# Text\n\n" + body + "\n"
    if notes:
        md += "\n# Notes\n\n" + "\n\n".join(notes) + "\n"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write(md)
    return os.path.relpath(path, os.path.join(out, "federal", "constitution")), title

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="dir with the three NARA .html pages")
    ap.add_argument("--out", default="legal")
    a = ap.parse_args()
    run_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    idx = []

    # --- Preamble + Articles
    url = NARA + "constitution-transcript"
    L = lines_of(os.path.join(a.src, "constitution-transcript.html"))
    s = next(k for k, l in enumerate(L) if l.startswith("We the People"))
    e = next(k for k, l in enumerate(L) if l.startswith("The Word, \"the,\""))
    L = L[s:e]
    idx.append(write(a.out, "preamble.md", "U.S. Const. pmbl. — Preamble", "U.S. Const. pmbl.",
                     "Preamble to the Constitution of the United States", L[0], url, run_at))
    art, sec, buf = None, None, []
    def flush():
        if art is None or not buf:
            return
        body = "\n\n".join(buf)
        if sec is None:
            idx.append(write(a.out, f"article-{ROMAN[art]}.md", f"U.S. Const. art. {art}",
                             f"U.S. Const. art. {art}", body[:200], body, url, run_at))
        else:
            idx.append(write(a.out, f"article-{ROMAN[art]}/sec-{sec}.md",
                             f"U.S. Const. art. {art}, § {sec}", f"U.S. Const. art. {art}, § {sec}",
                             body[:200], body, url, run_at))
    for l in L[1:]:
        m = re.match(r"^Article\.\s*([IVX]+)\.?$", l)
        n = re.match(r"^Section\.\s*(\d+)\.?$", l)
        if m:
            flush(); art, sec, buf = m.group(1), None, []
        elif n:
            flush(); sec, buf = n.group(1), []
        else:
            buf.append(l)
    flush()

    # --- Amendments I–X (ratified text only, after the "Amendment I" heading)
    url = NARA + "bill-of-rights-transcript"
    B = lines_of(os.path.join(a.src, "bill-of-rights-transcript.html"))
    s = next(k for k, l in enumerate(B) if l == "Amendment I")
    amend = []
    for l in B[s:]:
        m = re.match(r"^Amendment ([IVX]+)$", l)
        if m:
            amend.append([m.group(1), []])
        elif amend and not amend[-1][1]:
            amend[-1][1].append(l)          # each of I–X is a single paragraph
        elif amend and amend[-1][0] == "X":
            break
    for r, body in amend:
        txt = "\n\n".join(body)
        idx.append(write(a.out, f"amendment-{ROMAN[r]}.md", f"U.S. Const. amend. {r}",
                         f"U.S. Const. amend. {r}", txt[:200], txt, url, run_at,
                         notes=["Ratified December 15, 1791 (Bill of Rights)."]))

    # --- Amendments XI–XXVII
    url = NARA + "amendments-11-27"
    A = lines_of(os.path.join(a.src, "amendments-11-27.html"))
    s = next(k for k, l in enumerate(A) if l.startswith("AMENDMENT XI"))
    e = next(k for k, l in enumerate(A) if l.startswith("Back to Constitution Main Page"))
    cur = None
    groups = []
    for l in A[s:e]:
        m = re.match(r"^AMENDMENT ([IVXL]+)$", l)
        if m:
            cur = [m.group(1), [], []]; groups.append(cur); continue
        if cur is None:
            continue
        if re.match(r"^(Passed by Congress|Originally proposed|Note:|\*|Ratified)", l):
            cur[2].append(l)
        elif re.match(r"^Section \d+\.?$", l):
            cur[1].append(f"**{l}**")
        else:
            cur[1].append(l)
    for r, body, notes in groups:
        txt = "\n\n".join(body)
        repealed = r == "XVIII"
        p, t = write(a.out, f"amendment-{ROMAN[r]}.md", f"U.S. Const. amend. {r}",
                     f"U.S. Const. amend. {r}", re.sub(r"\*\*Section \d+\.\*\* ?", "", txt)[:200].replace("**", ""),
                     txt, url, run_at, notes=notes)
        if repealed:   # Prohibition, repealed by the 21st
            fp = os.path.join(a.out, "federal", "constitution", p)
            txt0 = open(fp, encoding="utf-8").read()
            open(fp, "w", encoding="utf-8").write(txt0.replace('status: "stable"', 'status: "deprecated"', 1))
        idx.append((p, t))

    with open(os.path.join(a.out, "federal", "constitution", "index.md"), "w", encoding="utf-8") as f:
        f.write("# Constitution of the United States\n\n" +
                "\n".join(f"* [{t}]({p})" for p, t in idx) + "\n")
    print(f"{len(idx)} constitutional concepts written "
          f"({sum(1 for p,_ in idx if p.startswith('amendment'))} amendments)")

if __name__ == "__main__":
    main()

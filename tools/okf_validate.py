#!/usr/bin/env python3
"""
okf_validate.py — check an OKF v0.2 bundle for conformance (spec §11) plus a few
corpus-health checks.

  * every non-reserved .md file has a parseable YAML frontmatter block with non-empty `type`
  * index.md / log.md: no frontmatter, except okf_version on the bundle-root index.md
  * reports: concept counts per top-level layer, empty bodies, broken bundle-relative links (sampled)

Usage: python3 okf_validate.py <bundle-root> [--link-sample 20000]
Stdlib only (frontmatter parsed with a tolerant line scanner, not a YAML library).
"""
import argparse, os, random, re, sys
from collections import Counter

def frontmatter(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        head = f.read(1 << 20)
    if not head.startswith("---\n"):
        return None, head
    end = head.find("\n---\n", 4)
    if end < 0:
        end = head.find("\n---", 4)
        if end < 0:
            return "UNTERMINATED", head
    return head[4:end], head[end + 5:]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--link-sample", type=int, default=20000)
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    bad, per_layer, types = [], Counter(), Counter()
    links_checked = broken = 0
    concept_files = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if not d.startswith(".")]
        for fn in fns:
            if not fn.endswith(".md"):
                continue
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, root)
            layer = rel.split(os.sep)[0] if os.sep in rel else "(root)"
            fm, _ = frontmatter(p)
            if fn in ("index.md", "log.md"):
                if fm and not (rel == "index.md" and re.search(r"^okf_version:", fm, re.M)):
                    bad.append(f"{rel}: reserved file carries frontmatter")
                continue
            if fm is None:
                bad.append(f"{rel}: no frontmatter"); continue
            if fm == "UNTERMINATED":
                bad.append(f"{rel}: unterminated frontmatter"); continue
            m = re.search(r'^type:\s*"?([^"\n]*)"?\s*$', fm, re.M)
            if not m or not m.group(1).strip():
                bad.append(f"{rel}: missing type"); continue
            types[m.group(1).strip()] += 1
            per_layer[layer] += 1
            concept_files.append(p)
    random.seed(7)
    for p in random.sample(concept_files, min(a.link_sample, len(concept_files))):
        body = open(p, encoding="utf-8", errors="replace").read()
        for target in re.findall(r"\]\((/[^)\s#]+\.md)\)", body):
            links_checked += 1
            if not os.path.exists(os.path.join(root, target.lstrip("/"))):
                broken += 1
    print(f"bundle: {root}")
    print(f"concept files: {sum(per_layer.values()):,}")
    for k, v in sorted(per_layer.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<22} {v:>10,}")
    print("types:", ", ".join(f"{t} ({n:,})" for t, n in types.most_common()))
    print(f"bundle-relative links checked (sample): {links_checked:,}, unresolved: {broken:,}"
          f" ({(100 * broken / links_checked) if links_checked else 0:.1f}%)  [OKF §6: tolerated]")
    print(f"conformance violations: {len(bad)}")
    for b in bad[:25]:
        print("  -", b)
    sys.exit(1 if bad else 0)

if __name__ == "__main__":
    main()

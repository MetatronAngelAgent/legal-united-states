# Legal United States

**An open, agent-readable corpus of United States law** — statutes, regulations, court rules,
case law and agency decisions — built from official and openly licensed sources into the
[Open Knowledge Format (OKF v0.2)](https://github.com/GoogleCloudPlatform/open-knowledge-format):
plain Markdown files with YAML frontmatter, one legal provision per file, ready to chunk, embed
and index for retrieval-augmented generation (RAG) and agent search.

This repository holds the **builder** (converters + one runner) and **sample output**. The full
corpus is generated locally — it is tens of GB and millions of files, too large for git.

```bash
cd tools
python3 build_legal.py --dry-run                 # plan + disk / iCloud checks
python3 build_legal.py --root ~/legal            # build everything (hours; resumable)
python3 build_legal.py --layers usc,cfr          # or selected layers
python3 okf_validate.py ~/legal                  # OKF conformance + link check
```

Requirements: Python 3.9+ (stdlib only). Optional `pdftotext` (poppler) for the San Diego layer.
Set `LEGAL_US_CONTACT` (an email or URL) so source sites can identify you in the User-Agent:
`export LEGAL_US_CONTACT=you@example.com`.

## What one file looks like

```markdown
---
type: "Statute Section"
title: "18 U.S.C. § 1030 — Fraud and related activity in connection with computers"
citation: "18 U.S.C. § 1030"
jurisdiction: "united-states"
jurisdiction_level: "federal"
status: "stable"            # deprecated = repealed / reserved / superseded
resource: "https://uscode.house.gov/..."
sources: [ ...official source, retrieval time, license... ]
checksum: "sha256…"
---
# Text
(a) Whoever— …[15 U.S.C. 1681](/federal/usc/title-15/sec-1681.md) …
# History
…
```

Every concept carries its citation, jurisdiction, status, provenance and license, so an index
can filter ("current California law only", "federal regulations amended since 2025") and an
agent can cite. Cross-references are rewritten as bundle-relative links, giving a citation graph:
Federal Register rules link to the CFR parts they amend; court opinions link to every U.S.C.,
C.F.R. and California code section they cite (76% resolve to an exact section in testing).

## Layers

| Layer | Source | Scale | License |
|---|---|---|---|
| `constitution` | National Archives transcription | 52 provisions | public domain |
| `usc` | OLRC U.S. Code release point (USLM XML) | 60,459 sections, all 54 titles | public domain |
| `court-rules` | USC appendix titles | 544 rules (FRAP, FRCP, FRE, FRCrP, FRBP) | public domain |
| `cfr` | govinfo eCFR bulk XML | 227,286 sections + 4,101 appendices | public domain |
| `fr` | govinfo Federal Register bulk XML | 2000–present (~20k docs/yr) | public domain |
| `ca-codes` | California Legislative Information (`pubinfo`) | Constitution + 29 codes, 162,526 sections | public domain |
| `san-diego` | San Diego City Clerk | 6,012 sections | public domain |
| `ca-manifest` | Census + code-publisher directories | all 482 CA cities + 58 counties → publisher | metadata |
| `case-law` | Caselaw Access Project (Common Pile) | CA + federal by default; `--caselaw-scope all` for every state | public domain |
| `agency` | Pile of Law | ~180k decisions/opinions/guidance, 18 bodies | docs public domain; **compilation CC BY-NC-SA 4.0** |
| `bva` | Pile of Law | Board of Veterans' Appeals | as above |
| `eval` | LegalBench | 162 legal-reasoning tasks | CC BY 4.0 |

Counts are from verification runs; see `docs/coverage.md` for validation results and known gaps.

## Coverage — honestly

**Complete:** federal statutes, regulations, court rules, Constitution, Federal Register;
California statutes and Constitution; San Diego's municipal code.

**Not yet:** the other 49 states' codes, constitutions and regulations (each needs its own
official-source converter); California regulations (CCR) and most city codes (behind
commercial-publisher terms — `docs/ca-municipal-manifest.csv` maps every CA city's route);
case law after ~2018 (CourtListener converter planned); agency decisions after 2022.

**Not included here:** the retrieval layer itself (embedding index + MCP query server) — the
corpus is prepared for it; see the roadmap in `docs/coverage.md`.

## Principles

- Official or openly licensed sources only; robots.txt respected; identifying User-Agent; polite rate limits.
- Bulk channels over scraping (e.g. govinfo bulk data rather than ecfr.gov's robots-disallowed bulk API).
- Nothing silently dropped: every layer writes a gaps report; repealed law is kept with `status: deprecated`.
- Source-data quality problems are fixed or flagged per concept (`text_quality`), never hidden.

## Repository layout

```
tools/      build_legal.py (runner) + 13 converters + okf_validate.py
samples/    real output: full Constitution and federal court rules, plus a few concepts per layer
docs/       coverage & validation notes, CA municipal publisher manifest
```

## Not legal advice

This corpus is research infrastructure. Texts are reproduced from official sources but may lag
the authoritative publication; always verify against the official source linked in each file.

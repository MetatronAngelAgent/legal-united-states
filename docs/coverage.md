# Coverage, validation and roadmap

## Validation results (verification runs, October 2026)

| Layer | Result |
|---|---|
| California codes | 162,526 / 162,526 sections written — exact match to the dump's own table of contents, all 30 codes |
| U.S. Code | 60,459 sections; written == parsed on every title. Only empty section: 19 U.S.C. § 1202 (Harmonized Tariff Schedule, not printed in the Code) |
| Court rules | 544 rules; current (e.g. FRE 702 includes the Dec. 1, 2023 amendment) |
| CFR | 227,286 sections + 4,101 appendices; HTML and GPO tables rendered as Markdown tables. Remaining text-free items are FAR-style group headings and image-only appendices (listed in `gaps-cfr.md`) |
| Federal Register | 2026 test year: 196 issues, 20,396 documents; page citations verified 112/112 against the XML |
| San Diego | 6,012 sections; independent audit against the source PDFs: 0 missing, 0 empty, 0 duplicates |
| Agency (Pile of Law) | 179,422 documents after dedup. Source fixes: FTC texts stored as Python byte literals (decoded); `taxrulings` is actually 78% IRS written determinations + 22% Tax Court opinions (split into separate folders); redaction-heavy IRS rulings kept (an early readability filter would have dropped 12,141 of them) |
| Case law | Two-shard test: 37,960 opinions (CA 23,741 + federal 14,219); 29,669 statute citations extracted, 22,569 linked to an exact section |
| Colorado | 35,392 sections from the official 2026 OLLS release; 340 repealed stubs kept as deprecated; 518 long annotation blocks split into companion concepts |
| States (Vaquill) | 2,825,039 / 2,825,039 rows written across 52 jurisdictions; 0 records without an official source URL; 0 empty; 286,739 not-in-force kept as deprecated; breadcrumb-based hierarchy (fixed 12,505 cross-code collisions found in testing) |
| Whole bundle | OKF §11 conformance: 0 violations across 1.58M federal concept files, and again across 3.66M files after adding all states (21 GB) |

## Rejected sources

- **Pile of Law `state_code`** — 2009–2014 snapshots, no state labels, unattributed origin. Unsafe to serve as current law.
- **ecfr.gov bulk API** — disallowed by its robots.txt; the same data comes from govinfo bulk data.
- **Municipal code publishers** (Municode/CivicPlus, ICC: American Legal, General Code, Code Publishing, Quality Code) — terms prohibit bulk copying; route is licensing, self-publishing cities, or CPRA requests.

## Roadmap

1. **Retrieval layer** — hybrid index (embeddings + BM25) with frontmatter filters; MCP server exposing `search_law`, `get_section(citation)`, `cites(citation)`, `cited_by(citation)`; LegalBench evaluation with vs. without retrieval.
2. **All-state case law** — `--caselaw-scope all` (~64 GB).
3. **Current case law** — CourtListener bulk opinions (post-2018).
4. **Commercial-clean state codes** — replace the CC BY-NC `states` layer state by state from official sources, bulk/API states first (see `state-sources.md`); then the four statute gaps (AR, GA, MS, TN). One converter per state, same frontmatter contract.
5. **Self-publishing cities** — same pattern as San Diego, prioritised by population from the manifest.

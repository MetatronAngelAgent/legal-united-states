# Coverage, validation and what is still incomplete

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
| Colorado | 35,392 sections from the official 2026 OLLS release; 340 repealed stubs kept as deprecated; 518 long annotation blocks split into companion concepts |
| States (Vaquill) | 2,825,039 / 2,825,039 rows written across 52 jurisdictions; 0 records without an official source URL; 0 empty; 286,739 not-in-force kept as deprecated; breadcrumb-based hierarchy (fixed 12,505 cross-code collisions found in testing) |
| Gap states (AR, GA, MS, TN) | 128,476 sections (AR 37,462 · GA 29,074 · MS 27,844 · TN 34,096), every section heading in each release converted. 1,147 Arkansas sections whose text the standardized HTML had dropped were recovered from Public.Resource.Org's own conversion of the same release. 92 sections still have no text in any available source (AR 8, GA 19, MS 5, TN 60) and say so. Sampled cross-references: 17,650 / 17,650 resolve to a section file |
| State regulations (new) | Louisiana 26,675 sections from 154 official .docx files (TOC lists 26,743 §-entries; the difference is reserved/duplicate TOC lines); Missouri 11,156 rules from 1,609 chapter PDFs (every rule heading found in the PDFs written, except TOC-only rescinded entries); Rhode Island 11,901 sections from 1,131 active Parts; Massachusetts 17,807 sections from 1,491 regulations (18 regulations had no identifiable current text); Hawaii 7,141 sections from 557 chapter PDFs across 19 departments — 101 scanned chapters without a text layer are skipped (25 OCR'd so far; `--ocr` does the rest). Multi-column PDF pages are cut at their gutters so text reads in order |
| Arkansas amendments | Code Sections Amended tables for 12 sessions (2021–2026): 6,386 sections changed → 4,993 snapshot sections flagged with the official Acts, 374 repealed since, 1,175 new sections listed in `arkansas/statutes/changes-since-snapshot.md` |
| Case law (CourtListener) | Sample of the first 20,000 rows of the 2026-09-30 opinions file: 19,045 written, 590 skipped as privacy-blocked by CourtListener, 365 skipped as empty/one-line; 26 jurisdictions represented; OKF conformance 0 violations |
| Whole bundle | OKF §11 conformance: 0 violations across 1.58M federal concept files, 3.66M files after adding all states, and the gap-state + case-law runs (170,871 files) |

## What is still incomplete

1. **Commercial-clean current statutes for 45 states + DC + PR.** These come from the `states` layer
   (Vaquill, current, official-URL sourced, but CC BY-NC 4.0 — non-commercial). Official-source
   replacements exist only for California and Colorado so far; `state-sources.md` lists each
   state's official bulk channel for building the rest.
2. **Arkansas, Georgia, Mississippi and Tennessee statutes are snapshots, not current law.**
   Arkansas through 2020-11-06, Georgia 2022-11, Mississippi 2020-07-08 (Mississippi ended its
   electronic edition), Tennessee 2021-05-21. Current text is only on LexisNexis public-access
   portals whose terms forbid bulk copying. Every concept carries `currency` and `stale_after`.
   Vaquill's current release has no statutes for these four states either.
   Arkansas is partly mitigated: every section changed since 2020 is flagged with the Acts that
   changed it. Georgia, Mississippi and Tennessee publish no comparable section index; their
   session laws (Ga. Laws, MS General Laws, TN Public Chapters) are the remaining route.
3. **State regulations — 8 states still missing**, each for a reason outside our control:
   - **California** (CCR) and **New York** (NYCRR): only on Thomson Reuters' government Westlaw site
     (Cloudflare-protected, vendor terms). Route: bulk request to OAL / NY DOS, or Cornell LII
     (CC BY-NC-SA, 10 s crawl delay — weeks for these sizes). `tools/lii_regs_to_okf.py
     --state california|new-york` crawls LII politely and resumably; **not yet test-run**.
   - **New Jersey** (NJAC) and **Vermont**: only on LexisNexis (terms forbid robots; AI agents blocked).
   - **Texas** (TAC): Secretary of State portal; its robots.txt blocks AI agents and the portal's
     rules of use warn against unauthorized use. Route: Public Information Act request.
   - **Indiana** (IAC): JavaScript app over an undocumented API that refuses outside calls.
   - **Tennessee** and **Arizona**: official, free per-chapter PDFs, but both sites refuse requests
     from this cloud workspace (HTTP 403). Likely fetchable from a normal home connection; Arizona's
     site says its chapters are "for private use only".
   Partial: **Massachusetts** comes from mass.gov's *unofficial* copies (the official CMR is sold in
   print); **Hawaii** has no compiled code — chapters are gathered from each department's page, and
   departments whose pages refuse automated access (Law Enforcement, Corrections) or post no chapter
   files are missing.
4. **Case law is built by the runner, not shipped.** All ~12 million opinions (estimate from the
   compressed file) come to ~200 GB / ~25 million files and ~1–2 days of processing; validated here
   on a 20,000-opinion sample. Within CourtListener itself: state *trial* courts are thin (most do
   not publish opinions), and opinions CourtListener has blocked for privacy are skipped (~3%).
5. **Municipal codes:** only San Diego. The other 481 California cities, 58 counties and every
   other state's cities publish through commercial code publishers whose terms prohibit bulk
   copying; the route is licensing, self-publishing cities, or public-records requests.
6. **Agency decisions after 2022** (Pile of Law ends there); the Vaquill federal files
   (board decisions, IRS written determinations, etc.) could extend this, non-commercially.
7. **Federal Register** was verified on 2026; the runner builds 2000–present (`--fr-from`).

## Rejected sources

- **Pile of Law `state_code`** — 2009–2014 snapshots, no state labels, unattributed origin. Unsafe to serve as current law.
- **ecfr.gov bulk API** — disallowed by its robots.txt; the same data comes from govinfo bulk data.
- **law.resource.org** — its robots.txt asks AI crawlers to stay out; the same Public.Resource.Org releases are taken from GitHub (UniCourt) and archive.org instead.
- **Municipal code publishers** (Municode/CivicPlus, ICC: American Legal, General Code, Code Publishing, Quality Code) — terms prohibit bulk copying.
- **LexisNexis state-code portals** (AR, GA, MS, TN, and others) — terms prohibit bulk copying; anti-bot protected.

## Roadmap

1. **Retrieval layer** — hybrid index (embeddings + BM25) with frontmatter filters; MCP server exposing `search_law`, `get_section(citation)`, `cites(citation)`, `cited_by(citation)`; LegalBench evaluation with vs. without retrieval.
2. **Commercial-clean state codes** — replace the CC BY-NC `states` layer state by state from official sources, bulk/API states first (see `state-sources.md`). One converter per state, same frontmatter contract.
3. **Citation graph for case law** — CourtListener's `citation-map` file links every opinion to the opinions it cites.
4. **Self-publishing cities** — same pattern as San Diego, prioritised by population from the manifest.

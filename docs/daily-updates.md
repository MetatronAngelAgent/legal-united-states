# Daily updates

`tools/update_legal.py` keeps a built corpus current. Schedule it once with
`tools/install_daily.sh` and it runs every morning (macOS launchd, which also catches up after
the Mac was asleep; cron on Linux). Each run writes a dated change report to
`references/updates/<date>.md`, a machine-readable `references/updates/latest.json`, and posts a
macOS notification with the count of new and changed items.

```bash
cd tools
sh install_daily.sh --root ~/Documents/OpenKnowledge/Cuh/legal --time 05:40
python3 update_legal.py --dry-run            # what is due today
python3 update_legal.py                      # run now
python3 update_legal.py --feeds congress     # one feed
launchctl kickstart gui/$(id -u)/com.legal-united-states.daily   # trigger the scheduled job
```

Optional settings go in `~/.config/legal-united-states/env` (created by the installer, mode 600):

```
COURTLISTENER_TOKEN=…        # needed for daily court opinions — free at courtlistener.com → Profile → Developer Tools
LEGAL_US_CONTACT=you@…       # identifies the crawler to source sites
```

## Feeds

| Branch | Feed | Cadence | Source | What happens |
|---|---|---|---|---|
| Executive | `presidential-documents` | daily | Federal Register API + govinfo | New Executive Orders, Proclamations, Memoranda, Notices, Determinations, with EO/proclamation number, signing date, president and disposition ("Revokes EO 13508") |
| Executive | `federal-register` | daily | govinfo daily issue XML | Every issue since the last run (rules, proposed rules, notices, presidential documents); holidays skipped; today's issue retried next run if not yet posted |
| Executive | `cfr` | daily | eCFR titles API + govinfo eCFR XML | Titles whose `latest_amended_on` / issue date changed are re-fetched and rebuilt; the report lists every new, changed and removed section |
| Legislative | `congress` | daily | govinfo BILLS, BILLSTATUS, PLAW | Only files govinfo changed since the last run: new bill versions, bill status (actions, cosponsors, CRS summaries), new public and private laws. Rolls over to a new Congress automatically |
| Legislative | `us-code` | daily | uscode.house.gov | When the Law Revision Counsel posts a new release point, the U.S. Code and federal court rules are rebuilt and diffed |
| Legislative | `california-codes` | weekly | leginfo `pubinfo_daily` dump | All 29 codes + Constitution rebuilt from the newest dump and diffed (≈1.2 GB download) |
| Judicial | `court-opinions` | daily | CourtListener API v4 | Opinions filed or revised since the last run, every federal, state, territorial and tribal court; same paths and format as the bulk case-law layer. Skipped (with a note in the report) until a token is set |
| Judicial | `case-law-snapshot` | weekly | CourtListener bulk S3 | When a new quarterly snapshot appears, its opinions not already present are ingested in a background process |
| States | `state-law` | weekly | Hugging Face `vaquill/open-us-law` | A new dataset revision rebuilds the `states` layer in a background process |

## How it stays safe

- **Cursors, not clocks.** Every feed stores where it got to in `references/state/update-state.json`;
  a missed day is caught up next time. govinfo directory listings (time + size per file) are kept
  in `references/state/govinfo-*.json` so only changed files are fetched.
- **Swap on success.** Rebuilt layers (CFR titles, U.S. Code, California) are built beside the old
  copy; the old copy is restored if the build fails and deleted only after it succeeds. This also
  removes sections that were repealed upstream.
- **Isolation.** One feed failing is recorded in the report (with the error) and does not stop the
  others; the process exits non-zero so launchd/cron logs show it. A lock file prevents overlapping
  runs; multi-hour rebuilds run detached with their own lock and log in `references/logs/`.
- **Check.** Every file written in a run gets a frontmatter check; run `okf_validate.py` for the
  full conformance pass.
- **Indexes.** Converters rebuild `index.md` files from what is on disk, so incremental runs never
  drop earlier entries.

## First run

The first run records a baseline for `cfr`, `us-code` and `state-law` (nothing is rebuilt), loads
the current Congress from govinfo's bulk zips (~2.5 minutes, ~0.7 GB on disk for the 119th), and
picks up the last 30 days of presidential documents and the last week of the Federal Register.
Backfill the full presidential-document history once with
`python3 build_legal.py --layers presidential` and all public laws since the 113th Congress with
`--layers congress`.

## Not yet covered daily

- **State legislatures** (bills and session laws) and **governors' executive orders**: no free
  bulk feed covers all states; Open States (API key) is the candidate source.
- **State regulations and city codes** outside the weekly `states` refresh.
- **Congressional Record**, committee reports and hearings (govinfo API key needed).
- **Office of Legal Counsel opinions** after 2022 (justice.gov/olc).

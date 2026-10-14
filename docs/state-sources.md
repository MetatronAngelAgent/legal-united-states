# State statutes — official-source build plan (option C)

Goal: a copy of every state's code built directly from official sources, with no third-party
compilation license, so it can be used commercially. Source map adapted from Vaquill's
`coverage.yml` (Apache-2.0, github.com/Vaquill-AI/open-us-law); status column is ours.

Build order: official bulk downloads first (fast, reliable), then official APIs, then per-section HTML.

| State | Status | Source tier | Official bulk / source | Access notes |
|---|---|---|---|---|
| California (CA) | official pubinfo DB (built) | bulk_zip | https://downloads.leginfo.legislature.ca.gov/pubinfo_{year}.zip | Official bulk zip, no key, no geo-block. CAML markup; exclude CONS (that is the constitution). |
| Colorado (CO) | official OLLS HTML zip (built) | bulk_zip | https://content.leg.colorado.gov/ (official CRS SGML zip; a few Title 8/14/30/39 sections are PDF-only) | Official SGML zip, public domain / no fee since 2016, no key, no geo-block. Best refresh path of any state. |
| Indiana (IN) | to build | bulk_zip | https://iga.in.gov/ic/{year}/{year}-Indiana-Code-html.zip | Official year-templated zip. US-geo-fenced: non-US IPs get a decoy HTML shell, so validate the zip magic bytes and fetch from a US IP. No ke |
| New Jersey (NJ) | to build | bulk_zip | https://www.njleg.gov/legislative-downloads?downloadType=Statutes (daily zip -> STATUTES.RTF) | Official daily zip; parse the RTF (style tags disambiguate title/section/body). Proxy only for the initial download. |
| New York (NY) | to build | api | https://legislation.nysenate.gov/ OpenLegislation API (/api/3/laws, ?full=true, ?date= point-in-time, /updates) | Free API key; full lettered-section coverage. Firewalled from non-US (US IP/proxy). This is the fix for the section-thinness. |
| South Dakota (SD) | to build | api | https://sdlegislature.gov/api/Statutes/{title}-{chapter}.html (per-chapter TOC, JSON-backed) + DisplayStatute.aspx (per  | Clean deterministic API. The .html suffix is the content path; bare /api/Statutes/{id} returns the SPA shell. Not blocked. |
| Utah (UT) | to build | api | https://le.utah.gov/data/developer.htm (official XML API, by title/chapter/section) + per-section HTML/PDF | Official XML API is the 'official electronic record'; no key, soft guidance <=1 query/day. US-geo-block (proxy). Official-source ingest scri |
| Massachusetts (MA) | to build | api | https://malegislature.gov/api (official REST API; Swagger at /api/swagger/, Chapters/Sections endpoints) | Official REST API, no key, no geo-block. Third-party mirrors of this code are stale - use the API. |
| Connecticut (CT) | to build | api | https://data.ct.gov/d/7qmh-sgdi (Socrata open-data API) + cga.ct.gov/current/pub/titles.htm (per-title HTM) | Socrata API is the clean bulk source. cga.ct.gov is WAF/anti-bot (headers/proxy). US-geo-restricted. |
| Texas (TX) | to build | ftp | ftp://ftp.legis.state.tx.us + per-code HTML zips (legis.state.tx.us/billlookup/filedownloads.aspx) | Official FTP/zip, free, no key. Per-code granularity. |
| Illinois (IL) | to build | ftp | https://www.ilga.gov/ftp/ILCS/ static tree + aReadMe/'Section Sequence.txt' manifest | Static file tree (no API/zip); use the manifest to enumerate every section. Site US-geo-restricts (proxy). |
| Washington (WA) | to build | bulk_pdf | https://app.leg.wa.gov/rcw/ (per-title) + Code Reviser RCW Archive PDFs | Official, free, no key, no geo-block, updated ~twice a year. |
| Oklahoma (OK) | to build | bulk_pdf | https://www.oklegislature.gov/OK_Statutes/CompleteTitles/os{NN}.pdf (official per-title PDFs) | Official per-title PDFs; OSCN per-section HTML is the structured fallback. No data API. |
| Maine (ME) | to build | bulk_pdf | legislature.maine.gov/statutes (official per-title PDF + MS Word RTF/DOC) + per-section HTML | Predictable per-title document URLs. No zip/API. |
| District of Columbia (DC) | to build | git | https://github.com/DCCouncil/law-xml-codified (official open-data XML, 569+ releases) + law-xml / dc-law-html | Best-tier git/zip, public, no key, no geo-block. Section total is exactly recountable. |
| Puerto Rico (PR) | to build | pdf | official OGP portal (bvirtualogp.pr.gov) per-title PDFs + statedepartment.pr.gov | Per-title PDFs, anti-bot, bilingual (Spanish/English duplication hazard on ingest). No API/zip. |
| Wyoming (WY) | to build | bulk_pdf | https://wyoleg.gov/statutes/compress/title{01..43}.pdf (official per-title PDFs) + NXT gateway HTML fallback | Official per-title PDFs, US-hosted, not anti-bot. Parse the PDF. Deterministic W.S. cites. |
| Mississippi (MS) | no bulk; official host has a broken TLS chain from our network — CC BY v2026.07 snapshot | html | no first-party bulk; per-section via billstatus.ls.state.ms.us. The compiled Miss. Code has no free official bulk feed. | No official zip/API - the weakest refresh path in the set. |
| Nevada (NV) | to build | html | https://www.leg.state.nv.us/NRS/NRS-{chapter}.html (per-chapter HTML) | No API/zip, no geo-block. Dedupe to distinct NRS x.y before counting. |
| Alabama (AL) | to build | html | alison.legislature.state.al.us (per-section HTML) + Local_Laws_Index.pdf | No official zip. Anti-bot moderate (proxy). |
| Louisiana (LA) | to build | html | legis.la.gov (per-section HTML) | Direct-serving (no proxy). No zip/API. |
| Michigan (MI) | to build | html | https://www.legislature.mi.gov/documents/mcl/ (per-chapter XML tree) + per-section RTF/PDF + annual MCLT PDF (enumerates | Near-bulk XML tree. Host TLS chain is incomplete (pin the chain or use a tolerant client). No geo-block. |
| Maryland (MD) | to build | html | mgaleg.maryland.gov (per-section HTML + PDF, keyed by article code gag/gcl/grp/gtr/...) | No bulk zip/XML, no geo-block. Article + section is the citation key. |
| Arkansas (AR) | LexisNexis public-access portal (anti-bot) — CC BY v2026.07 snapshot or licensed feed | html | free public access portal (per-section HTML) | JavaScript/anti-bot, no zip/API (third-party mirrors are bot-walled) - needs a headless browser or a licensed feed. |
| Oregon (OR) | to build | html | https://www.oregonlegislature.gov/bills_laws/ors/ors{NNN}.html (per-chapter) + volume PDFs | No zip/API/FTP, no geo-block. |
| New Mexico (NM) | to build | html | nmonesource.com (per-section HTML; JavaScript single-page app) | JavaScript SPA (Selenium/headless), fragile. No public bulk/API. No geo-block. |
| Virginia (VA) | to build | html | law.lis.virginia.gov/vacode (HTML + JSON) | No zip. Reconcile by node id, not by state tag (va also holds the Admin Code ~22k and the constitution). |
| Ohio (OH) | to build | html | codes.ohio.gov (per-section HTML, LAWriter) + certified per-page PDF on demand | No zip/API. US-only geo-block (proxy). Official-source ingest script available. |
| Tennessee (TN) | LexisNexis public-access portal (anti-bot) — CC BY v2026.07 snapshot or licensed feed | html | free public access portal (per-section HTML) | JavaScript/anti-bot, no zip/API - needs a headless browser or a licensed feed. |
| Montana (MT) | to build | html | mca.legmt.gov (per-section HTML); the official bulk feed is paid, and free only to MT/tribal government | No free zip/API. Deep paths are anti-bot (headers/proxy). |
| South Carolina (SC) | to build | html | https://www.scstatehouse.gov/code/title{NN}.php -> per-section .php | No zip/API. Decimal title-chapter-section numbering. |
| Missouri (MO) | to build | html | https://revisor.mo.gov/main/OneSection.aspx?section={c.s} (per-section) / OneTitle.aspx?title= | No zip/API. Each chapter renders as ~8 tables - read all of them, not just the first (old scraper bug). |
| North Dakota (ND) | to build | html | https://ndlegis.gov/cencode/t{N}c{CC}.html and .pdf (per-chapter HTML + PDF) | No zip/API, no geo-block. |
| Iowa (IA) | to build | html | legis.iowa.gov (per-chapter XML/HTML) + 8-vol PDF / per-chapter PDF-RTF | No single zip/API. Geo-fenced but not bot-walled (proxy). |
| Minnesota (MN) | to build | html | https://www.revisor.mn.gov/statutes/cite/{chap.sec} (per-section) + authenticated per-chapter/volume PDFs | DB sold commercially, so no free zip/API. No geo-block. |
| Nebraska (NE) | to build | html | https://nebraskalegislature.gov/laws/statute.php?statute={chapter-section} (per-section) + search_range_statute.php (ran | No API/zip. US-geo/bot-restricted, needs a US IP/proxy. Citation-keyed URLs, deterministic. |
| West Virginia (WV) | to build | html | https://code.wvlegislature.gov/{c-a-s}/ (per-section) + wvcodeentire.htm (full-code dump) | No API/zip. Bot user-agent gets 403 (realistic headers/proxy). |
| New Hampshire (NH) | to build | html | https://www.gc.nh.gov/rsa/html/ (full static HTML tree, mirrorable) + gencourt.state.nh.us/downloads/ (data/SQL exports) | Mirrorable HTML tree + data downloads. US-geo-restricted (proxy). |
| Florida (FL) | to build | html | leg.state.fl.us per-section (.../{range}/{chapter}/Sections/{ch}.{sec}.html) OR flsenate.gov/Laws/Statutes/2025/Chapter{ | Official 'Statutes Download' is FLLawDL2025.zip -> setup.exe (Windows installer, dead-end). Use per-section HTML. No data API. |
| Kansas (KS) | to build | html | https://www.ksrevisor.gov/statutes/chapters/ch{NN}/{path}.html (path encodes chapter/article/section) | No zip/API (SoS sells print). Fully deterministic per-section URL tree. |
| Vermont (VT) | to build | html | https://legislature.vermont.gov/statutes/section/{title}/{chapter}/{section} (per-section) + per-title TOC | No zip/API. Deterministic slugs. |
| Idaho (ID) | to build | html | legislature.idaho.gov/statutesrules/idstat (per-title / per-chapter HTML) | No zip/API. Title 15 (UPC) nests Chapter>Part>Section (flatten). id tag also holds IDAPA regs + constitution - reconcile by node id. |
| Arizona (AZ) | to build | html | https://www.azleg.gov/arsDetail/?title={NN} (whole-title HTML) + ROC combined statute-book PDF (roc.az.gov) | No API/zip, no block. Site is maintained for drafting and points elsewhere for the certified official version. |
| Delaware (DE) | to build | html | https://delcode.delaware.gov/title{N}/c{NNN}/index.html (per-chapter) + legis.delaware.gov/json/DelCodeSearch/GetDelCode | No bulk zip, no geo-block. Title 8 is the DGCL. |
| Rhode Island (RI) | to build | html | https://webserver.rilegislature.gov/Statutes/TITLE{NN}/INDEX.HTM (per-title/chapter HTML) | No zip/API, no geo/bot. Deterministic paths. |
| Kentucky (KY) | to build | html | https://apps.legislature.ky.gov/law/statutes/statute.aspx?id={id} (per-section HTML) | No zip/API. The page id is an opaque internal number NOT derivable from the KRS cite - crawl the chapter index each run to map cites to ids. |
| Wisconsin (WI) | to build | html | docs.legis.wisconsin.gov/statutes (per-chapter HTML + PDF; no single bulk file) | No zip/API. Proxy concurrency ceiling ~20; phase-separate crawl then chunk. |
| Alaska (AK) | to build | html | https://www.akleg.gov/basis/statutes.asp?title={N} (per-title HTML, BASIS) | JS-rendered BASIS tree (Selenium/headless). No bulk zip/API. Geo (proxy). |
| Hawaii (HI) | to build | html | https://www.capitol.hawaii.gov/hrsall/ (per-chapter HTML, ChaptersByVolume.aspx?id={NN}) + LRB DOCX volumes | No zip/API, no geo/bot. |
| Pennsylvania (PA) | to build | html | consolidated: palegis.us view-statute?ttl={NN}&chpt=&sctn= (HTML/PDF/Word, deterministic); unconsolidated: browse by Act | No statute bulk (the /data page is only bill-history XML). WAF (proxy). Unconsolidated ids (Act Year / P.L.) are NOT reproducible. |
| Georgia (GA) | exclusive commercial publisher; Public.Resource.Org has 2019 release only — CC BY v2026.07 snapshot | html | no free official bulk. The O.C.G.A. is published under an exclusive commercial arrangement, so there is no first-party f | No free official bulk feed. Freshness gap, not a coverage gap. |
| North Carolina (NC) | to build | html | https://www.ncleg.gov/EnactedLegislation/Statutes/HTML/ByChapter/Chapter_{N}.html (per-chapter, bulk-friendly) | No zip/API. Mirror ncleg.net. |

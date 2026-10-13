#!/usr/bin/env python3
"""
ca_municipal_manifest.py — Jurisdiction -> code-publisher manifest for California.

Builds the "sources manifest" layer for CA municipal law: every incorporated city
(and all 58 counties) mapped to who publishes its municipal/county code and where,
joined against the Census population list. This is discovery metadata only — it
reads the publishers' own public client DIRECTORIES (one page each), not any code
content. The code text itself stays behind each publisher's terms; the lawful
acquisition routes per jurisdiction are the publisher licensing programs, codes
the city self-publishes on its own .gov site, and CPRA requests.

Inputs (pass paths, or let it fetch):
  --census    sub-est2024.csv  (www2.census.gov popest "cities/totals")
  --icc       ICC Code Solutions text-library HTML (icccodesolutions.org/text-library/)
              -> covers all three ICC platforms: American Legal (codelibrary.amlegal.com),
                 General Code (ecode360.com), Code Publishing (codepublishing.com)
  --municode  Municode/CivicPlus client JSON (api.municode.com/Clients/stateAbbr?stateAbbr=ca)

Outputs (into <out>/references/):
  ca-municipal-manifest.csv   one row per jurisdiction
  ca-municipal-manifest.md    OKF concept: coverage report + unmatched list
Stdlib only.
"""

import argparse, csv, datetime as dt, json, os, re, sys
from collections import defaultdict

def norm(name):
    n = re.sub(r"\s+", " ", name or "").strip()
    n = re.sub(r"^(City of|Town of|City and County of)\s+", "", n, flags=re.I)
    n = re.sub(r"\s+(city|town|County)$", "", n, flags=re.I)
    n = n.replace("ñ", "n").replace("’", "'")
    return n.strip().lower()

def key_of(name, is_county):
    return norm(name) + ("|county" if is_county else "|muni")

def jtype(raw):
    r = (raw or "").lower()
    if "county" in r:
        return "county"
    if r.startswith("town of") or r.endswith(" town"):
        return "town"
    return "city"

def municode_url(name):
    return "https://library.municode.com/ca/" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--census", required=True)
    ap.add_argument("--icc", required=True)
    ap.add_argument("--municode", required=True)
    ap.add_argument("--out", default="ok")
    args = ap.parse_args()
    run_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ---- Census: CA incorporated places (SUMLEV 162) + counties (050) ----
    places = {}      # norm -> (display, type, pop)
    with open(args.census, encoding="latin-1") as f:
        rdr = csv.DictReader(f)
        popcol = next(c for c in rdr.fieldnames if c.upper().startswith("POPESTIMATE")
                      and c[-4:].isdigit())
        popcols = sorted([c for c in rdr.fieldnames if c.upper().startswith("POPESTIMATE")])
        popcol = popcols[-1]
        for r in rdr:
            if r.get("STNAME") != "California":
                continue
            lev = r.get("SUMLEV")
            nm = r.get("NAME", "")
            if lev == "162":
                t = "town" if nm.endswith(" town") else "city"
                places.setdefault(key_of(nm, False), (re.sub(r"\s+(city|town)$", "", nm), t,
                                                      int(r.get(popcol) or 0)))
            elif lev == "050":
                places.setdefault(key_of(nm, True), (nm, "county", int(r.get(popcol) or 0)))

    # ---- ICC Code Solutions text library (CA section): AmLegal + eCode360 + CodePub ----
    h = open(args.icc, encoding="utf-8", errors="replace").read()
    i = h.find('class="state CA"')
    j = h.find('class="state CO"', i)
    seg = h[i:j if j > i else len(h)]
    icc = {}         # norm -> (display_raw, platform, url)
    for url, raw in re.findall(r'href="(http[^"]+)"[^>]*>\s*([^<]+?)\s*<', seg):
        plat = ("american-legal" if "amlegal.com" in url else
                "ecode360" if "ecode360.com" in url else
                "code-publishing" if "codepublishing.com" in url else None)
        if not plat:
            continue
        icc.setdefault(key_of(raw, 'county' in raw.lower()), (raw.strip(), plat, url))

    # ---- Municode / CivicPlus client API JSON ----
    mjs = json.load(open(args.municode, encoding="utf-8"))
    muni = {}
    for c in mjs:
        nm = c.get("ClientName", "")
        muni.setdefault(key_of(nm, 'county' in nm.lower()), (nm, "municode", municode_url(nm)))

    # consolidated city-county (San Francisco): a match on either key covers both
    for store in (icc, muni):
        for k in list(store):
            base, cls = k.rsplit("|", 1)
            twin = base + ("|muni" if cls == "county" else "|county")
            if twin in places and twin not in store and "city and county" in store[k][0].lower():
                store[twin] = store[k]

    # ---- join ----
    rows, by_pub = [], defaultdict(int)
    universe = dict(places)
    extras = []  # publisher clients not in the Census place list (districts, JPAs…)
    for key in sorted(set(icc) | set(muni)):
        if key not in universe:
            src = icc.get(key) or muni.get(key)
            extras.append((src[0], src[1], src[2]))
    # cities that self-publish their code and are already ingested into this bundle
    SELF_PUBLISHED = {"san diego|muni": ("self-published:sandiego.gov",
                                         "https://www.sandiego.gov/city-clerk/officialdocs/municipal-code",
                                         "san-diego")}
    for key, (disp, t, pop) in sorted(universe.items(), key=lambda kv: -kv[1][2]):
        pubs = []
        if key in icc:
            pubs.append(("icc:" + icc[key][1], icc[key][2]))
        if key in muni:
            pubs.append(("civicplus:municode", muni[key][2]))
        route = "publisher-license-or-cpra" if pubs else "check-city-site-or-cpra"
        if key in SELF_PUBLISHED:
            label, url, folder = SELF_PUBLISHED[key]
            pubs = [(label, url)]
            if os.path.isdir(os.path.join(args.out, "california", "municipal", folder)):
                route = f"ingested:california/municipal/{folder}"
            else:
                route = "self-published:ingestible"
        if pubs:
            for p, _ in pubs:
                by_pub[p] += 1
        else:
            by_pub["none-found"] += 1
        rows.append({
            "jurisdiction": disp, "type": t, "population": pop,
            "publisher": ";".join(p for p, _ in pubs) or "none-found",
            "code_url": ";".join(u for _, u in pubs),
            "acquisition_route": route,
        })

    ref = os.path.join(args.out, "references")
    os.makedirs(ref, exist_ok=True)
    csvp = os.path.join(ref, "ca-municipal-manifest.csv")
    with open(csvp, "w", newline="", encoding="utf-8") as f:
        wtr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wtr.writeheader()
        wtr.writerows(rows)

    cities = [r for r in rows if r["type"] in ("city", "town")]
    counties = [r for r in rows if r["type"] == "county"]
    cov_c = sum(1 for r in cities if r["publisher"] != "none-found")
    cov_k = sum(1 for r in counties if r["publisher"] != "none-found")
    unmatched = [r for r in rows if r["publisher"] == "none-found"]

    md = [
        "---",
        'type: "Reference"',
        'title: "California municipal code publisher manifest"',
        'description: "Every CA city and county mapped to its municipal-code publisher and URL; discovery metadata for the municipal layer."',
        'tags: ["california", "municipal", "manifest"]',
        'jurisdiction: "california"',
        'jurisdiction_level: "municipal"',
        f'generated: {{ by: "ca_municipal_manifest/1.0", at: "{run_at}" }}',
        "sources: ",
        '  - { id: census, resource: "https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/cities/totals/sub-est2024.csv", title: "Census population estimates (incorporated places + counties)", author: "process:us-census" }',
        '  - { id: icc-library, resource: "https://www.icccodesolutions.org/text-library/", title: "ICC Code Solutions client library (American Legal + General Code/eCode360 + Code Publishing)", author: "process:icc-code-solutions" }',
        '  - { id: municode-api, resource: "https://api.municode.com/Clients/stateAbbr?stateAbbr=ca", title: "Municode/CivicPlus CA client directory", author: "process:civicplus" }',
        "---",
        "",
        "# Coverage",
        "",
        f"- Cities/towns: {cov_c} of {len(cities)} have a publisher-hosted code "
        f"({100*cov_c//max(1,len(cities))}%)",
        f"- Counties: {cov_k} of {len(counties)}",
        "- Publisher counts: " + ", ".join(f"{k}={v}" for k, v in sorted(by_pub.items())),
        f"- Publisher clients outside the Census place list (districts, JPAs, etc.): {len(extras)}",
        "",
        "# Acquisition routes (per the gaps policy)",
        "",
        "- The code **text** on these platforms is not bulk-collectable under their terms;",
        "  this manifest records where each code lives, not its content.",
        "- Routes: publisher licensing/API programs; codes a city self-publishes on its own",
        "  .gov site; California Public Records Act requests to the city clerk.",
        "- Known blind spot: former Quality Code Publishing clients (qcode.us) are not in the",
        "  merged ICC directory, so several of the jurisdictions below (e.g. Bakersfield,",
        "  Chula Vista) are actually publisher-hosted there; others (e.g. San Diego)",
        "  self-publish on their own .gov site. Verify per city before filing a request.",
        "",
        "# Jurisdictions with no publisher match",
        "",
    ]
    for r in unmatched:
        md.append(f"- {r['jurisdiction']} ({r['type']}, pop {r['population']:,}) — check city/county site")
    md += ["", "Full table: [ca-municipal-manifest.csv](ca-municipal-manifest.csv)", ""]
    with open(os.path.join(ref, "ca-municipal-manifest.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    print(f"cities/towns: {len(cities)} (covered {cov_c}) | counties: {len(counties)} "
          f"(covered {cov_k}) | extras: {len(extras)}")
    print("by publisher:", dict(by_pub))
    print("unmatched:", len(unmatched))

if __name__ == "__main__":
    main()

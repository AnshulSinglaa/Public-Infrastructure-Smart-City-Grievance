# Coverage wards (v1, 8 wards)

MCD 2022 delimitation numbering. Built by `python scripts/fetch_wards.py`, which also re-runs the checks below.

| Ward | Name | Why | How verified |
|---|---|---|---|
| 185 | Madanpur Khadar East | Contains the Kalindi Kunj / Yamuna ghat (ghat + garbage scenario) | Point-in-polygon: 28.544N 77.311E falls in 185 only (not 186 or 187); Kalindi Kunj Bridge (OSM) runs through it |
| 186 | Madanpur Khadar West | Next to the ghat; Mathura Rd (PWD) edge vs. colony lanes | OSM Mathura Road (trunk) nodes fall inside the ward |
| 187 | Sarita Vihar | Mathura Rd (≥60 ft, PWD) vs. inner colony lanes, next to the ghat | OSM Mathura Road (trunk) nodes fall inside the ward |
| 180 | Badarpur | Mathura Rd / MB Rd boundary (pothole on a PWD road) | OSM Mathura Road (trunk + primary) nodes fall inside the ward |
| 184 | Jaitpur | Dense colony lanes, no big road: streetlight / inner-lane scenario | No Mathura/Ring/MB Rd nodes inside the ward in OSM |
| 144 | Lajpat Nagar | Ring Rd area: Lala Lajpat Rai Path + Mathura Rd vs. colony lanes | OSM Mathura Road and Lala Lajpat Rai Path nodes inside the ward |
| 143 | Sidhartha Nagar | Mathura Rd stretch (Ashram side) through MCD colonies | OSM Mathura Road (primary) nodes inside the ward |
| 145 | Andrews Ganj | Touches the NDMC boundary (agency-edge test); Lodhi Rd | Shares an edge with the NDMC charges (distance 0 m, Census-2011-based NDMC polygons, see below) |

## Sources

- **Wards:** OpenCity "Delhi MCD Wards Map - 2022" (KML, public domain, from GSDL/SEC delimitation maps). The live OpenCity file now returns 404, so the script downloads the Wayback Machine copy (`delhi_wards.kml`, capture 2025-06-22). Raw file: `data/raw/delhi_wards_2022_archive.kml`.
- **MCD zone** (`mcd_zone`): OpenCity "Delhi MCD Zones Map - 2022", Wayback copy, joined by ward centroid.
- **Roads used for checks:** OpenStreetMap via Overpass, 2026-10-24 (ODbL).
- **NDMC edge check only:** `data/raw/transit_delhi_wards.geojson` (GitHub arshilyusuf/TransitAnalyzer, Census-2011 ward layer with 9 NDMC charges). Used to verify ward 145. Not shipped as a zone.

## Known gaps / approximate

- **Only 189 of 250 wards.** The archived KML was cut at 5 MiB by Common Crawl, so 61 wards are missing (17–25, 32–36, 42–48, 61–63, 70–78, 95–97, 146–149, 154, 161–164, 168–176, 193–195, 204–207). All 8 coverage wards are complete. Fix: get the full file (7,173,765 bytes) from OpenCity or GSDL and drop it at `data/raw/delhi_wards_2022_archive.kml`; the script re-reads it.
- **Zones archive cut at 1 MiB:** 10 of the 12 zone polygons are present, so 34 wards have `mcd_zone = ""`. All coverage wards have one (Central).
- **`name_hi`** is empty: the source has no Hindi names.
- **Kalindi Kunj ghat polygon is hand-drawn** (`approximate: true`) around the ghat point, checked against OSM (bridge, riverbank, park). No official DDA/I&FC polygon was found.
- **NDMC boundary skipped:** OSM has no NDMC boundary relation (only a "NDMC Society" neighbourhood). Next option: dissolve the 9 NDMC charges above (Census 2011, third-party copy, licence unclear) or digitise from the NDMC map.
- The source also has one unnamed polygon with ward number 0 (AC 44 New Delhi). The script skips it.

## Roads and the PWD list (Sprint 2)

- **`osm_roads.geojson`**: `python scripts/import_osm.py`. One Overpass query (bbox of the 8 wards, `trunk` … `service` + `*_link`), raw answer cached in `data/raw/overpass_roads.json` (2026-10-27, ODbL). A way is kept if any point is in a coverage ward. `ward_no` is the ward of its midpoint, or of its first point inside coverage if the midpoint is outside.
- **`pwd_list.csv`**: 66 rows copied from PWD Delhi's official **"Roads with PWD"** inventory (https://www.pwddelhi.gov.in/maintenance-inventory/roads-with-pwd, 1,297 roads, read 2026-10-27). The page loads its table with JavaScript and has no export, so the rows were read in a browser and only those near the coverage wards were kept. The verbatim copy is in `data/raw/pwd/roads_with_pwd_extract_2026-10-27.tsv`. `page` is the page number at 100 rows per page in the default order.
  - **This is not the 2012 notification of roads with a RoW of 60 ft and above.** That notification (GNCTD, 10.01.2012) was not found online. The inventory also lists some narrower roads (Jaitpur School Road is 10.86 m, for example), so `row_ge_60ft` is recorded per row from the page's RoW column (18.29 m = 60 ft). Flyover rows have no RoW.
  - `match_name` is curator-added and used only for matching (for example, Ring Road = Mahatma Gandhi Marg in OSM). It is not part of the source.
- **`pwd_matches.csv`**: `python scripts/match_pwd.py`. difflib ≥ 0.85 on normalised names. `confirmed` is empty until the curator fills it in. Extra columns: `pwd_road_id`, `ward_no`. A way that matches several stretches of the same road (Mathura Road has 3 rows) gets one proposal per stretch.

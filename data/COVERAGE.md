# Coverage wards (v2, 25 wards; all 250 ward polygons loaded)

MCD 2022 delimitation numbering. Build order (from the repo root):
`fetch_wards.py` → `build_zones.py` → `import_osm.py` → `fetch_pwd.py` → `match_pwd.py` → `load_wards.py`.
`fetch_wards.py` re-runs the ward checks (ghat point in 185, coverage rings valid).

"Agency types" = which zone/road types from `DEPARTMENTS.md` a ward lets us demo. Zone hits were checked
with PostGIS (zone within 20 m of the ward). Discoms are by licence area (see Known gaps).

| Ward | Name | MCD zone (old name) | Discom | Why / agency types it demonstrates |
|---|---|---|---|---|
| 185 | Madanpur Khadar East | Central | BRPL | Kalindi Kunj ghat (ghat + garbage); **Yamuna floodplain (DDA + I&FC)**, DMRC Kalindi Kunj station |
| 186 | Madanpur Khadar West | Central | BRPL | Mathura Rd (NH-2, **PWD** list, not NHAI) vs colony lanes; railway line; DMRC |
| 187 | Sarita Vihar | Central | BRPL | Mathura Rd PWD vs inner lanes; DDA sports complex (**DDA land**); railway; DMRC |
| 180 | Badarpur | Central | BRPL | Mathura Rd / MB Rd boundary (pothole on a **PWD** road) |
| 184 | Jaitpur | Central | BRPL | Dense colony lanes, no big road (**MCD** streetlight / inner lane); floodplain edge |
| 144 | Lajpat Nagar | Central | BRPL | **Ring Road (PWD)**; Barapullah (**I&FC trunk drain**); CPWD Srinivaspuri edge; DMRC; railway |
| 143 | Sidhartha Nagar | Central | BRPL | Mathura Rd (Ashram); Barapullah (**I&FC**); floodplain; DMRC Sarai Kale Khan; railway |
| 145 | Andrews Ganj | Central | BRPL | **NDMC edge**; **CPWD colonies** (Lodhi Colony, Kasturba Nagar, Sadiq Nagar); Barapullah; DMRC |
| 18 | Jahangir Puri | Civil Line | TPDDL | **JJ cluster (DUSIB)** Bhalaswa jhuggi; DDA green land; **TPDDL** |
| 30 | Bawana | Narela | TPDDL | **Industrial area (DSIIDC)** Bawana; Bawana Drain + Bawana Escape (**I&FC**); JJ resettlement colony; TPDDL |
| 34 | Nangloi | Narela | BRPL | Mundka **industrial area**; Mungeshpur / Madanpur / Karari Suleman Nagar drains (**I&FC**); JJ; DMRC Green line; railway |
| 57 | Pitam Pura | Keshavpuram | TPDDL | **Outer Ring Road (PWD)**; **DMRC** Red line stations; TPDDL |
| 73 | Civil Lines | City & Sadar | TPDDL | **Ridge/forest** (Kamla Nehru Ridge); **ASI** (Qudsia Bagh, Ashoka pillar); Ring Road; floodplain; DMRC; railway |
| 74 | Chandni Chowk | City & Sadar | BYPL | **ASI** Red Fort; DMRC; railway (Old Delhi); floodplain edge; **BYPL** |
| 82 | Pahar Ganj | Karolbagh | BYPL | **NDMC edge**; **railway** (New Delhi station yard); DMRC; BYPL |
| 96 | Rajouri Garden | West | BRPL | **Ring Road (PWD)**; DMRC Blue/Pink; **West** zone |
| 127 | Najafgarh | Najafgarh | BRPL | Mungeshpur drain (**I&FC**); DMRC Grey line; rural-urban **Najafgarh** zone |
| 133 | Mahipalpur | Najafgarh | BRPL | **NH-48 (NHAI**, gazette 22-06-2026); **Cantonment edge**; DMRC Airport line; railway |
| 139 | Naraina | Karolbagh | BRPL | **Cantonment edge**; Ring Road; railway; DMRC; next to Naraina industrial area |
| 152 | R.K. Puram | South | BRPL | **NDMC edge**; **CPWD colony** (Moti Bagh); Ring Road / ORR; **South** zone |
| 155 | Mehrauli | South | BRPL | **ASI** Qutb / Mehrauli Archaeological Park; **ridge/forest**; DDA land |
| 156 | Vasant Kunj | South | BRPL | **Ridge/forest** (Southern Ridge); Cantonment edge; NH-48 nearby; DMRC Yellow; railway |
| 197 | Patpar Ganj | Sh South | BYPL | **Yamuna floodplain (east bank)**; DDA land; DMRC Mayur Vihar; NH-9 (ref only, owner not confirmed); BYPL |
| 213 | Shastri Park | Sh South | BYPL | Floodplain; **DMRC** Shastri Park depot/stations; **railway**; ASI Salimgarh edge |
| 226 | Gautam Puri | Sh North | BYPL | Floodplain edge; **Shahdara North** zone; BYPL |

Every type appears at least once: NDMC (82, 145, 152), Cantonment (133, 139, 156), floodplain DDA + I&FC
(185, 143, 197, 213), NHAI NH-48 (133), PWD Ring Rd / ORR (144, 96, 57, 152), DMRC (57, 213 + most),
railway (82, 213, 186), DSIIDC industrial (30, 34), DUSIB JJ (18, 30), ridge/forest (73, 155, 156),
ASI (155, 74, 73), CPWD (145, 152), BRPL / BYPL / TPDDL, and 11 of the 12 old MCD zones (all but Rohini)
(Central, Civil Line, Narela, Keshavpuram, City & Sadar, Karolbagh, West, Najafgarh, South, Sh South, Sh North).

## Sources

- **Wards (all 250):** OpenCity "Delhi MCD Wards Map - 2022" KML (public domain, from GSDL/SEC delimitation
  maps). OpenCity now 404s and the Wayback copy is cut at 5 MiB, so `fetch_wards.py` downloads the full
  file (7,173,765 bytes) re-published on Kaggle (`wigglerofgems/open-city-delhi-wards-shape-dataset-kml`),
  saved as `data/raw/delhi_wards_2022_kaggle.zip`. Its first 5 MiB are byte-identical to the Wayback copy.
  Ward numbers/names cross-checked with the SEC 2022 ward population table (e.g. 145 Andrews Ganj,
  185 Madanpur Khadar East).
- **MCD zone** (`mcd_zone`, old 2022 names): OpenCity "Delhi MCD Zones Map - 2022" (Wayback, cut at 1 MiB)
  joined by ward centroid. The cut file lacks the West and South zone polygons; the unmatched wards are
  exactly 92–116 and 148–173, so they are set to West and South. New 2026 zone names: see DEPARTMENTS.md.
- **Special zones:** `scripts/build_zones.py` → `data/special_zones.geojson` (each feature has `source`
  and `approximate`). Table below.
- **Roads:** OpenStreetMap via Overpass (ODbL), one query = union of one bbox per coverage ward, now
  including `motorway` (NH-48 is tagged motorway) and the `ref` tag. A `ref` alone does NOT mean NHAI.
- **PWD list:** all 1,297 roads of PWD Delhi's official "Roads with PWD" inventory, read from the JSON
  endpoint the page itself calls (`/api/maintenance/roads/search`, `scripts/fetch_pwd.py`, raw answer in
  `data/raw/pwd/roads_with_pwd_api.json`). The 66 hand-copied rows from v1 are kept with their `match_name`.
  Being on the list = PWD, whatever the width; `row_ge_60ft` is informational only.

## Special zones

| type | features | approximate | source |
|---|---|---|---|
| ghat | 1 | true | hand-drawn (v1) |
| ndmc_area | 1 | true | DataMeet Delhi_Wards (9 NDMC charges, 2017 layer, CC BY) |
| cantonment | 1 | true | DataMeet Delhi_Wards (8 Delhi Cantt charges, 2017 layer) |
| yamuna_floodplain | 1 | true | OSM Yamuna river area + 300 m and centre line + 500 m (no official O-zone polygon found) |
| ifc_trunk_drain | 11 drains | true | names from I&FC Flood Control Order 2025 Annexure B (`data/ifc_trunk_drains.csv`, 62 drains) matched to OSM waterways, buffered 15 m |
| asi_protected | 78 | true | OSM `historic=*` named features matched to the ASI list of centrally protected monuments, Delhi section (asi.nic.in/pdf/CPM_List.pdf), + 100 m prohibited area. Name matching is fuzzy: curate |
| metro_premises | 215 stations | false | OSM DMRC stations (polygons as mapped, points + 60 m) |
| railway_land | 1 | false | OSM `landuse=railway` + main tracks buffered 12 m |
| industrial_area | 9 | false | OSM named industrial estates (Bawana, Mundka, Wazirpur, Mayapuri, Patparganj, Narela, GT Karnal Rd, …) |
| jj_cluster | 19 | true | OSM areas named JJ / jhuggi; DUSIB publishes no polygons |
| cpwd_colony | 21 | true | OSM residential areas / place nodes (+300 m) named after GPRA colonies |
| dda_land | 36 | true | OSM areas with operator DDA |
| forest_ridge | 1 | true | OSM landuse=forest / nature_reserve / protected_area, clipped to Delhi |
| discom_area | 3 (BRPL, BYPL, TPDDL) | true | union of the coverage wards per discom, by licence area; has `department_code` |

**Not obtained:** discom boundaries outside the coverage wards (no public GIS), official Yamuna O-zone,
official ridge notification boundary, DUSIB JJ-cluster polygons, ASI protected-limit maps, R.K. Puram
GPRA sectors (not mapped in OSM), DMRC viaduct footprint. 51 of the 62 I&FC drains have no named OSM line.

**For the backend:** `discom_area` and `ifc_trunk_drain` are lookups (rules SL-/EL-/DR-01), not owner zones.
Exclude them from the "pin → special zone" step or every pin in coverage would match a discom zone.

## Known gaps / approximate

- All 250 wards present (`wards.geojson`, 250 features). The source also has an unnamed polygon with ward
  number 0 (AC 44 New Delhi); it is skipped.
- Some source ward rings self-touch; `load_wards.py` stores `ST_MakeValid` geometry.
- `name_hi` is empty: the source has no Hindi names.
- NDMC/Cantt are 2017 boundaries next to 2022 MCD wards, so edges can overlap by tens of metres.
- `data/raw/try/` holds the previous agent's trial downloads (2017 ward layers, cut KMLs); not used.

## Roads and PWD matching (v2, 2026-10-29)

- `osm_roads.geojson`: 18,909 ways (2,007 named, 210 with `ref`) across the 25 wards; NH-48 (ref NH48,
  `motorway`) ways fall in 133 Mahipalpur (11) and 156 Vasant Kunj (7).
- `pwd_list.csv`: 1,297 rows (66 v1 + 1,231 from the inventory JSON). `pwd_matches.csv`: 1,398 proposals on
  666 OSM ways for 153 PWD road names (181 road IDs), all `confirmed` empty for the curator. Generic PWD
  names (e.g. "Mahatma Gandhi Road", 81 ways) need a curator `match_name` to stop over-matching.

## States (Sprint 4, outside coverage)

- `data/states.geojson` (36 states/UTs) and the `state_boundary` table are built by `scripts/load_states.py`
  from Natural Earth 10m admin-1 v5 (public domain), simplified ~1 km (0.01°) in PostGIS.
- It already has the post-2019 units: J&K (JK) and Ladakh (LA) separate, DNH&DD merged (DH).
- Codes are the current ISO 3166-2:IN ones (2023-11 change): CT→CG, OR→OD, TG→TS, UT→UK.
- **Boundary issues:** Natural Earth draws de facto lines (Line of Control in Kashmir, Aksai Chin, parts of
  Arunachal), not India's official map, so pins in those areas may find no state → CPGRAMS only. Each state
  is simplified on its own (GEOS 3.11 has no coverage simplify), so slivers up to ~1 km can open between
  neighbours, and a pin right on a state border may hit none or two. A nearest-state fallback within ~2 km
  would fix that if it matters. Coastal pins within ~1 km of the shore can fall outside every state.
- `data/state_directory.csv`: 29 of 36 verified (portal page fetched on a .gov.in / .nic.in domain on
  2026-10-29, and the page itself shows it is a grievance portal). Helplines are filled only where the official
  page shows the number. Not verified (blank URL, `verified=no`): LD, NL, SK, WB (no state-wide portal found),
  MN (CM Da Haisi was under maintenance), ML (megpgrams.gov.in, linked from meghalaya.gov.in, did not load),
  and TN (the CM Helpline is on tnega.org, which is not a government domain). Saved pages: `data/raw/states/portals/`.

## PWD match curation (Sprint 5, 2026-10-30)

`scripts/match_pwd.py` now fills `confirmed` in `pwd_matches.csv` (a curator's own `yes` / `no` is kept on re-runs):

| confirmed | proposals |
|---|---|
| `auto` | 570 (on 327 OSM ways → `road_segment.pwd_listed`) |
| unconfirmed (empty) | 677 |
| `rejected` | 151 |

- **auto** = score 1.0 (exact normalised name), exactly one PWD row with that name, way in a coverage ward,
  name not generic. **Exception:** `STRETCH_ROADS` (Ring, Outer Ring, Mathura, Lala Lajpat Rai, Rani Jhansi
  flyover, Rohtak, New Rohtak, Maharaja Agarsen, Narela–Bawana) are one road split into several PWD stretch rows,
  so any stretch counts as PWD. All their stretch proposals are auto; `load_rules.py` takes the lowest road_id
  and the smallest RoW.
- **rejected** = the PWD row's division is on the opposite side of Delhi from the ward (e.g. North West Road
  Division "Bhagwan Mahavir Marg" matched to a way in Vasant Kunj): same name, other road. This is a coarse
  compass check from the inventory's division field, not geometry.
- **Generic-name blocklist** (normalised, never auto): mahatma gandhi, gurudwara, main, link, service, mandir,
  shiv mandir, gopal mandir, mata mandir, hospital, church, idgah, power house, central, mall, main bazar,
  naya bazar, shani bazar, arya samaj, sadar thana, school, station, market, internal, approach, pusta,
  kotla, plus any name containing gali / "no <digit>" / block / pocket / sector.
- "Mahatma Gandhi Marg" is also Ring Road's OSM name (about 400 proposals). It stays unconfirmed because other
  MG roads exist; a curator can confirm the Ring Road ways by hand. Sri Aurobindo Marg has no exact-score match.
- Spot-check of 15 auto matches against the inventory's PWD division: Mathura Rd (Badarpur), Ring Rd (Civil Lines),
  LLR Path (Andrews Ganj), Outer Ring Rd (Pitam Pura), Lodhi Rd, Nelson Mandela Marg, Aruna Asaf Ali Marg,
  Rohtak Rd (Nangloi), Najafgarh–Bahadurgarh Rd, Sarita Vihar Rd, Chandni Chowk, Siri Fort Rd, Jaitpur Rd and
  Madanpur Khadar Rd all fit their division. Bhagwan Mahavir Marg (Vasant Kunj vs North West division) was
  wrong; the division check now rejects it.

### Ring Road route relation (2026-10-30)

- OSM route relations Ring Road Delhi (1208531, 331 ways) and Delhi Outer Ring Road (1208532, 223 ways); members
  are cached in `data/raw/ring_road_relations.json`. A proposal becomes `confirmed=auto_route` only when its way
  is a member of the relation AND the PWD row is a Ring Road (or Outer Ring Road) stretch. The
  "Mahatma Gandhi Marg" blocklist still applies everywhere else.
- Result: 127 `auto_route` proposals on 42 ways. 53 of the 95 Ring Road member ways in the coverage wards are now
  PWD-listed. The other 42 are flyovers / underpasses / unnamed links with no name proposal (e.g. Ashram
  Railway Flyover, Moolchand Underpass); a curator could add them.
- Outer Ring Road has no naming problem (its ways are already named "Outer Ring Road" → `auto`). 4 of its 24
  member ways in coverage are confirmed; the rest are named Palam Marg / flyovers and get no proposal.
- Totals now: auto 570, auto_route 127, rejected 151, unconfirmed 550; 369 PWD-listed road segments.
- Snap check: 28.570N 77.250E is about 400 m from Ring Road, so it snaps to colony lanes. A point on the
  Ring Road carriageway near Lajpat Nagar (28.5714N 77.2568E) snaps at 2 m to Mahatma Gandhi Marg,
  `pwd_listed`, RD-00504.
- **Route-relation rule (load_rules.py):** every member way of Ring Road relation 1208531 in the coverage wards is
  now PWD-listed with `row_source = route_relation`, whether or not its name matched (flyovers, underpasses,
  unnamed links). It takes the `pwd_road_id` and `row_m` of the nearest name-confirmed Ring Road way. This adds
  42 ways, so 411 segments are PWD-listed (369 `pwd_list` + 42 `route_relation`).
- Outer Ring Road (1208532) is unchanged: its 13 PWD stretch rows leave gaps (e.g. Africa Avenue → NH-8,
  Burari → Badli), so they don't clearly cover the whole route.
- Lajpat Nagar check: 28.5714N 77.2568E snaps (4.9 m) to way 312031901, PWD (RD-00504). The parallel
  carriageway way 37751255 (3–9 m away) is NOT a member of relation 1208531 in OSM, so it stays unlisted.
  A pin on that side within 25 m still finds the PWD way, but nearest-first snapping can pick 37751255.
  Fix options: add it to the OSM relation upstream, or have a curator mark it `yes` in pwd_matches.csv.

# Departments, categories and routing rules (draft, 2026-10-27)

Three files:

- `departments.csv` (32 agencies): who exists and how to reach them.
- `categories.csv` (37 problem types): what a citizen can report.
- `rules_draft.csv` (73 rules): which agency gets which problem, and where.

All sources were read on 2026-10-27. `verified = yes` means every filled value in the row was seen on an official page/document or in a dated news report. A blank cell means "not confirmed", never "doesn't exist". `name_hi` values are standard translations and were not checked against a source.

Rules are read top-down: **a lower priority number is checked first, and the first match wins**. There is one exception: the `EMERGENCY OVERLAY` rules (EM-*) don't stop the search. They put 112 / 101 / the discom / IGL numbers on screen, and matching then carries on to find the owner. A `;` list in `category` becomes one DB row per category when loaded.

## How a problem reaches a department

```
┌──────────────────────────┐
│ photo + text + GPS pin   │
└────────────┬─────────────┘
             │ vision + text model gives a category
             ▼
┌──────────────────────────┐  gas / live wire / open manhole /
│ EM-* emergency overlay   │  cave-in / dangerous bldg / tree on wires
│ (never stops matching)   ├────────────► show 112 · 101 · discom · IGL now
└────────────┬─────────────┘
             │ continue
             ▼
┌──────────────────────────┐  pin inside Cantonment ──► DCB
│ AR-* area override       │  pin inside NDMC area  ──► NDMC / NDMC_ELEC
└────────────┬─────────────┘
             │ neither
             ▼
┌──────────────────────────┐  metro footprint ─► DMRC   railway land ─► NR
│ ZN-* special zone        │  ASI monument   ─► ASI    floodplain ─► DDA / IFC / MCD
│                          │  DDA land ─► DDA  DSIIDC estate ─► DSIIDC  JJ ─► DUSIB
└────────────┬─────────────┘
             │ no zone
             ▼
┌──────────────────────────┐  NHAI stretch (NH-48) ─────────────► NHAI
│ RD-* road snap (25 m)    │  in PWD inventory (any width) ─────► PWD
│                          │  not listed, row_m >= 18.29 m ─────► PWD (+MCD 2nd)
│                          │  not listed, narrower / gali ──────► MCD
└────────────┬─────────────┘  unknown ─► ask "main road or lane?"
             │ category decides
             ▼
┌──────────────────────────┐  sewer / manhole ─► DJB   water ─► DJB
│ category rules           │  big trunk nala ─► IFC    roadside nala ─► road owner
│ DR SW WT WS SL EL TR ... │  power / wire ─► discom of area (BRPL/BYPL/TPDDL)
│                          │  signal / parking ─► DTP  tree felling ─► FOREST
└────────────┬─────────────┘  cattle / dead animal ─► MCD_VET ...
             │ nothing matched
             ▼
┌──────────────────────────┐
│ GN-* general channels    │──► "no cited rule found" + PGMS (state) / CPGRAMS (central)
└──────────────────────────┘
```

## Corrections to the brief (found during research)

1. **"Storm drains > 4 ft = I&FC" is wrong.** MCD desilts its own drains that are 4 ft deep and above (713 to 793 of them; NGT report). I&FC runs only 57 named trunk drains (about 382 km). Rule: a trunk drain on the curated list → IFC. A drain along a PWD road → PWD (SDMC affidavit). Otherwise → MCD.
2. **Being on the PWD list means PWD, whatever the width.** The inventory is the list of roads PWD maintains. Width only matters for roads that are *not* listed (the 2012 ≥60 ft notification).
3. **Being a national highway does not mean NHAI.** NH-2 (Mathura Road) is in the PWD inventory. Only NH-48 (Dhaula Kuan to the Haryana border) is confirmed as handed to NHAI (MoRTH gazette, 22-06-2026).
4. **MCD zones were renamed in 2026.** The new names are Outer North, North, North-West, Central North, Old Delhi, Central, West, South-West, South-East, South, East and North-East (Pioneer, 22-07-2026). `wards.geojson` still uses the old names (e.g. "Central" for ward 185). The old→new mapping is **unverified**. No zone-level phone numbers were found, so every MCD row uses 155305 and MCD 311.
5. **The unified 311 / Delhi Mitra is not confirmed live.** It was announced in May 2025 and approved in Aug 2025 with a 2-month target, but no launch was found. The live services are MCD 311 and NDMC-311, which are separate apps.

## Unverified items (do not show these as facts)

| Item | Candidate value seen | Why unverified |
|---|---|---|
| ASI Delhi Circle phone / email | 011-24654832, circledel.asi@gmail.com | Only on ffo.gov.in location pages, not ASI |
| DSIIDC helpline | none found | Site unreachable (connection refused) |
| Transport Dept helpline | 011-42400400 | 2016 article only |
| Unified 311 / Delhi Mitra | 311 | Announced, launch not found |
| DTC toll-free | 1800118181, ccc@dtc.nic.in | Aggregators only; official site shows HQ 011-23370236 + feedback form |
| CPWD Sewa call centre | 1800114499 / 18002664499 | Search snippet; the portal page didn't show them |
| NDMC care email / WhatsApp | care@ndmc.gov.in, 8588887773 | Aggregators only |
| DCB phone / e-Chhawani desk | 011-25693837, 011-25683622 | Aggregators; delhi.cantt.gov.in refused connection |
| DJB email / WhatsApp | grievances-djb@delhi.gov.in | Aggregator; the official page lists none |
| PGMS 1031 hours | 24x7 vs 9:30–18:00 | Two govt pages disagree |
| Delhi Police noise line | 155271 | 2019 DoT allocation; current status unknown |
| LPG leak line | 1906 | Aggregator |
| MCD old→new zone mapping | — | Article gives new names only |
| Electricity inside Delhi Cantt | DCB or BRPL? | Not found (rule AR-02, conf 0.4) |
| Water/sewer inside Cantt | DCB / MES / DJB? | Not found |
| JJ-cluster toilets = DUSIB | — | Belief only (ZN-10, conf 0.4) |
| Fallen tree on PWD road = PWD | — | No source (TR-02) |
| Dead animal lifting = MCD Veterinary | — | No direct source (AN-02) |
| Garbage on PWD road lifted by MCD | — | Assumption (WS-01 note) |
| Footpath encroachment owner | road owner | No source (EN-01) |
| Bus shelter owner today | DTC vs concessionaire vs Transport Dept | Sources date from 2014–2019 |
| SLAs | — | No official resolution time found for any department except CPGRAMS (21 days). DERC Supply Code 2017 has power-restoration standards, but the PDF could not be parsed, so `sla_days` is left blank |

## Shared jurisdictions that remain genuinely ambiguous, and what the app does

| # | Ambiguity | App behaviour |
|---|---|---|
| 1 | **Drain: sewer (DJB) vs storm drain (IFC / PWD / MCD)** | Ask **one question** `drain_type`: "Big open nala, or sewer line / manhole?" This is asked before road_class. If the answer is not_sure, show **2 candidates** (DJB + road owner) |
| 2 | **Road: PWD vs MCD** for unlisted roads (no width in OSM) | Use the image tie-break first. If that doesn't settle it, ask `road_class`: "Main road or inner lane?" If the answer is not_sure, show 2 candidates |
| 3 | **Mathura Road (Ashram–Badarpur): PWD vs NHAI.** It is still in the PWD inventory, but NHAI gave in-principle approval to take it over | Always show **2 candidates** (PWD first, NHAI second) until a handover notice is found. This affects coverage wards 143, 180, 186 and 187 |
| 4 | **Yamuna ghat: MCD (bank) vs IFC (water) vs DDA (floodplain land)** | Ask `ghat_position`: "On the river bank or in the water?" If not_sure, show 2 candidates. The current split is from 2014–2017 reports |
| 5 | **Streetlight: discom vs PWD vs MCD.** Discoms maintain MCD lights, but we have no discom polygons | Ask "Which company sends your electricity bill?" (citizens know this from their bill). If not_sure, show MCD 311, which forwards it |
| 6 | **Parks: DDA vs MCD** (no DDA park polygons) | Show 2 candidates (MCD_HORT, DDA) |
| 7 | **NDMC / Cantonment edges** (the NDMC polygon isn't built yet) | Within 15 m of an edge, show 2 candidates. Inside the Cantonment, electricity and water are unknown, so show 2 candidates + general channels |
| 8 | **Bus shelters: DTC vs Transport Dept vs ad concessionaire** | Show 2 candidates (DTC, TRANSPORT), confidence 0.5 |
| 9 | **Garbage burning / pollution: MCD (owner) vs DPCC (Green Delhi app)** | One owner (MCD), with the Green Delhi app shown as an extra channel |
| 10 | **Problem type not in the list** | `OTHER` → general channels (PGMS / CPGRAMS). The app never guesses |

## Data still needed (for the data/scripts owner)

The rules refer to these `special_zone` types, and none of them has a polygon yet: `cantonment`, `ndmc_area`, `metro_premises`, `railway_land`, `asi_protected`, `dda_land`, `industrial_area`, `jj_cluster`, `cpwd_colony` and `forest_ridge`. Two more inputs are also missing: discom area polygons (BRPL / BYPL / TPDDL), and a curated list of the I&FC trunk drains. Until they exist, the matching rules simply don't fire, and the question / 2-candidate paths above take over.

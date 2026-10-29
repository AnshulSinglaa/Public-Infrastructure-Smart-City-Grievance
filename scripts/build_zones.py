"""Build data/special_zones.geojson (the zone types the routing rules need).

Run from the repo root:  python scripts/build_zones.py   (add --refresh to re-download)
Needs the local PostGIS (DATABASE_URL) for buffering and unions; it writes nothing to the DB.

Sources (all saved in data/raw/zones/, data only, never executed):
- ndmc_area, cantonment: DataMeet Municipal_Spatial_Data Delhi_Wards.geojson (2017 layer, has
  the 9 NDMC charges and 8 Delhi Cantt charges). Pre-2022 boundaries -> approximate.
- ifc_trunk_drain: OSM waterways whose name matches data/ifc_trunk_drains.csv
  (I&FC Flood Control Order 2025, Annexure B), buffered 15 m.
- asi_protected: OSM historic features whose name matches the ASI list of centrally protected
  monuments, Delhi section (asi.nic.in/pdf/CPM_List.pdf, text in asi_cpm_delhi_section.txt), + 100 m.
- everything else: one Overpass query over Delhi (OpenStreetMap, ODbL).
- discom_area: union of the coverage wards per discom (see DISCOM). NOT a jurisdiction map.
Hand-drawn features already in the file (type ghat) are kept as they are.
"""
import csv
import json
import os
import re
import urllib.request
from difflib import get_close_matches
from pathlib import Path

import psycopg
from fetch_wards import COVERAGE
from import_osm import overpass

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "zones"
OUT = ROOT / "data" / "special_zones.geojson"
DB = os.environ.get("DATABASE_URL", "postgresql://postgres:dev@127.0.0.1:5433/civic")
DATAMEET_URL = "https://raw.githubusercontent.com/datameet/Municipal_Spatial_Data/master/Delhi/Delhi_Wards.geojson"
DATAMEET = RAW / "datameet_Delhi_Wards.geojson"
BOX = "28.40,76.83,28.89,77.35"  # Delhi NCT (S,W,N,E)
OSM = "OpenStreetMap via Overpass, retrieved 2026-10-28 (ODbL)"

# General pool (GPRA) colonies maintained by CPWD; names only, verify against the CPWD list.
GPRA = ("R.? ?K.? ?Puram|Sarojini Nagar|Netaji Nagar|Kidwai Nagar|Laxmi ?bai Nagar|Lodhi Colony|Moti Bagh|"
        "Nauroji Nagar|Sri ?niwas ?puri|Sadiq Nagar|Aliganj|Kasturba Nagar|Thyagraj Nagar")
QUERY = f"""[out:json][timeout:300][bbox:{BOX}];
(
  way["waterway"]["name"];
  wr["landuse"="forest"]; wr["leisure"="nature_reserve"]; wr["boundary"="protected_area"];
  nwr["historic"~"^(monument|tomb|fort|castle|ruins|archaeological_site|city_gate|citywalls|mosque|tower|milestone|building)$"]["name"];
  wr["landuse"="industrial"]["name"]; wr["name"~"industrial (area|estate)|DSIIDC",i][!"highway"][!"route"];
  wr["landuse"="railway"]; way["railway"="rail"][!"service"];
  nwr["railway"="station"]["network"~"Delhi Metro",i]; nwr["railway"="station"]["operator"~"DMRC|Delhi Metro",i];
  wr["water"="river"]["name"~"Yamuna",i]; wr["waterway"="riverbank"]["name"~"Yamuna",i];
  wr["operator"~"^DDA$|Delhi Development Authority",i];
  wr["name"~"(^|[ (])(JJ|J.J.|J J)([ )]|$)|jhuggi",i];
  wr["landuse"="residential"]["name"~"{GPRA}",i]; node["place"]["name"~"{GPRA}",i];
);
out body geom;"""

# type: (priority, approximate, buffer for points m, buffer for polygons m, buffer for lines m or None = drop lines)
KIND = {
    "asi_protected": (20, True, 150, 100, None),    # protected limits + 100 m prohibited area
    "metro_premises": (25, False, 60, 0, None),
    "railway_land": (30, False, None, 0, 12),
    "ifc_trunk_drain": (35, True, None, 0, 15),
    "cantonment": (40, True, None, 0, None),
    "ndmc_area": (40, True, None, 0, None),
    "yamuna_floodplain": (50, True, None, 300, 500),  # river area + 300 m, centre line + 500 m
    "industrial_area": (55, True, None, 0, None),
    "jj_cluster": (55, True, None, 0, None),
    "cpwd_colony": (55, True, 300, 0, None),  # place node of a GPRA colony + 300 m
    "dda_land": (60, True, None, 0, None),
    "forest_ridge": (60, True, None, 0, None),
    "discom_area": (900, True, None, 0, None),  # attribute lookup, not an owner zone (see COVERAGE.md)
}
# Licence area per coverage ward (BRPL south + west, BYPL east + central, TPDDL north + north-west).
DISCOM = {
    "BRPL": {34, 96, 127, 133, 139, 143, 144, 145, 152, 155, 156, 180, 184, 185, 186, 187},
    "BYPL": {74, 82, 197, 213, 226},
    "TPDDL": {18, 30, 57, 73},
}
DISCOM_SOURCE = ("Coverage wards only, assigned by licence area (BRPL south/west, BYPL east/central, "
                 "TPDDL north/north-west); no official GIS boundary found")
assert set().union(*DISCOM.values()) == COVERAGE, "every coverage ward needs a discom"


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower().replace("nallah", "drain").replace("nala", "drain")
                  .replace("no.", "no").replace(" ii", " 2").replace(" i", " 1"))


# Words that don't identify a monument; the rest of an OSM name must all appear in the ASI list.
GENERIC = {"tomb", "of", "the", "gate", "mosque", "masjid", "fort", "qila", "baoli", "gumbad", "gumbaz", "ka", "ki",
           "ke", "darwaza", "wall", "walls", "and", "ruins", "complex", "monument", "mahal", "minar", "with", "unknown",
           "old", "delhi", "new", "bagh", "garden", "park", "shah", "khan", "sahib", "protected", "house", "dome",
           "domed", "dalan", "dalans", "gateway", "graves", "gumti", "hammam", "tombs", "chattri", "chhatri"}
ASI_WORDS = set(re.findall(r"[a-z]+", (RAW / "asi_cpm_delhi_section.txt").read_text(encoding="utf-8").lower()))


def asi_listed(name):
    """True if every distinctive word of the OSM name is (nearly) in the ASI Delhi list.
    ponytail: word-level fuzzy match, gives some false positives; curate the output if it matters."""
    words = [w for w in re.findall(r"[a-z]+", name.lower()) if w not in GENERIC and len(w) >= 4]
    return bool(words) and all(w in ASI_WORDS or get_close_matches(w, ASI_WORDS, 1, 0.85) for w in words)


def classify(tags, drains):
    """OSM tags -> (zone type, zone name) or None."""
    name = tags.get("name", "")
    if "yamuna" in name.lower() and tags.get("waterway") == "river":
        return "yamuna_floodplain", "Yamuna floodplain (approx.)"
    if "waterway" in tags and tags.get("waterway") != "riverbank":
        hit = get_close_matches(norm(name), drains, 1, 0.85)  # OSM spellings vary: Kirari/Karari, Mundhela
        return ("ifc_trunk_drain", drains[hit[0]]) if hit else None
    if "yamuna" in name.lower() and (tags.get("water") == "river" or tags.get("waterway") == "riverbank"):
        return "yamuna_floodplain", "Yamuna floodplain (approx.)"
    if tags.get("railway") == "station":
        return "metro_premises", f"{name} metro station"
    if tags.get("landuse") == "railway" or tags.get("railway") == "rail":
        return "railway_land", "Railway land and tracks"
    if "historic" in tags:
        return ("asi_protected", name) if asi_listed(name) else None
    if re.search(r"industrial (area|estate)|udyog", name, re.IGNORECASE) and "amenity" not in tags:  # named estates, not depots or factories
        return "industrial_area", name
    if re.search(r"^DDA$|Delhi Development Authority", tags.get("operator", ""), re.IGNORECASE):
        return ("dda_land", name) if name else None
    if re.search(r"(^|[ (])(JJ|J\.J\.|J J)([ )]|$)|jhuggi", name, re.IGNORECASE):
        return "jj_cluster", name
    if re.search(GPRA, name, re.IGNORECASE) and (tags.get("landuse") == "residential" or "place" in tags):
        return "cpwd_colony", re.sub(r"^(Sector|Block) [\w-]+,? ", "", name)  # one zone per colony
    boundary = tags.get("boundary") in ("protected_area", "national_park")
    if tags.get("landuse") == "forest" or tags.get("leisure") == "nature_reserve" or boundary:
        return "forest_ridge", "Ridge and forest areas"
    return None


def osm_geometry(el):
    """GeoJSON for an Overpass element: Point, LineString, Polygon or MultiLineString (relation)."""
    if el["type"] == "node":
        return {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
    if el["type"] == "way":
        line = [[p["lon"], p["lat"]] for p in el.get("geometry", [])]
        closed = len(line) >= 4 and line[0] == line[-1]
        return {"type": "Polygon", "coordinates": [line]} if closed else {"type": "LineString", "coordinates": line}
    lines = [[[p["lon"], p["lat"]] for p in m["geometry"]] for m in el.get("members", [])
             if m["type"] == "way" and m.get("geometry")]
    return {"type": "MultiLineString", "coordinates": lines}  # ST_BuildArea turns it into polygons


def pieces():
    """Yield (type, name, source, geojson, buffer_m)."""
    drains = {}
    with open(ROOT / "data" / "ifc_trunk_drains.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            drains[norm(r["drain_name"].split(" (")[0])] = r["drain_name"]
    for el in overpass(QUERY, RAW / "overpass_zones.json")["elements"]:
        hit = classify(el.get("tags", {}), drains)
        if not hit:
            continue
        ztype, zname = hit
        geom = osm_geometry(el)
        _, _, b_pt, b_poly, b_line = KIND[ztype]
        buf = {"Point": b_pt, "LineString": b_line}.get(geom["type"], b_poly)
        if buf is None:
            continue
        src = OSM + f", {el['type']}/{el['id']}" if ztype not in ("forest_ridge", "railway_land") else OSM
        if ztype == "ifc_trunk_drain":
            src = "I&FC Flood Control Order 2025 Annexure B (name) + " + OSM + " (line), buffered 15 m"
        yield ztype, zname, src, geom, buf

    if not DATAMEET.exists():
        RAW.mkdir(parents=True, exist_ok=True)
        DATAMEET.write_bytes(urllib.request.urlopen(DATAMEET_URL, timeout=120).read())
    for f in json.loads(DATAMEET.read_text(encoding="utf-8"))["features"]:
        no = f["properties"]["Ward_No"] or ""
        src = "DataMeet Municipal_Spatial_Data Delhi_Wards.geojson (2017 charges, CC BY): " + DATAMEET_URL
        if no.startswith("NDMC_"):
            yield "ndmc_area", "NDMC area", src, f["geometry"], 0
        elif no.startswith("CANT_"):
            yield "cantonment", "Delhi Cantonment", src, f["geometry"], 0

    wards = {f["properties"]["ward_no"]: f["geometry"]
             for f in json.loads((ROOT / "data" / "wards.geojson").read_text(encoding="utf-8"))["features"]}
    for code, nos in DISCOM.items():
        for no in nos:
            yield "discom_area", code, DISCOM_SOURCE, wards[no], 0
    for geom in wards.values():
        yield "_delhi", "", "", geom, 0  # only used to clip the OSM zones


def main():
    with psycopg.connect(DB) as conn:
        conn.execute("CREATE TEMP TABLE z (type text, name text, source text, geom geometry)")
        for ztype, zname, src, geom, buf in pieces():
            g = "ST_SetSRID(ST_GeomFromGeoJSON(%(g)s), 4326)"
            if geom["type"] == "MultiLineString" and not buf:
                g = f"ST_BuildArea({g})"
            if buf:
                g = f"ST_Buffer({g}::geography, %(b)s)::geometry"
            conn.execute(f"INSERT INTO z VALUES (%(t)s, %(n)s, %(s)s, ST_MakeValid({g}))",
                         {"t": ztype, "n": zname, "s": src, "g": json.dumps(geom), "b": buf})
        # Clip OSM zones to Delhi (the query bbox also covers Noida, Gurugram, Faridabad).
        rows = conn.execute("""
            WITH delhi AS (SELECT ST_Union(geom) AS g FROM z WHERE type IN ('_delhi', 'ndmc_area', 'cantonment')),
                 u AS (SELECT type, name, min(source) AS source,
                              ST_Intersection(ST_Union(geom), (SELECT g FROM delhi)) AS g
                       FROM z WHERE type <> '_delhi' GROUP BY type, name)
            SELECT type, name, source,
                   ST_AsGeoJSON(ST_Multi(ST_CollectionExtract(ST_MakeValid(
                       ST_SimplifyPreserveTopology(g, 0.00003)), 3)), 6)
            FROM u WHERE ST_Area(g::geography) > 2000""").fetchall()  # drop slivers < 0.2 ha

    keep = [f for f in json.loads(OUT.read_text(encoding="utf-8"))["features"] if f["properties"]["type"] == "ghat"]
    feats = keep + [{
        "type": "Feature",
        "properties": {"type": t, "name": n if t != "discom_area" else f"{n} (coverage wards)",
                       "priority": KIND[t][0], "source": s, "approximate": KIND[t][1],
                       **({"department_code": n} if t == "discom_area" else {})},
        "geometry": json.loads(g),
    } for t, n, s, g in rows if g and json.loads(g)["coordinates"]]
    feats.sort(key=lambda f: (f["properties"]["priority"], f["properties"]["name"]))
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, indent=1), encoding="utf-8")
    counts = {}
    for f in feats:
        counts[f["properties"]["type"]] = counts.get(f["properties"]["type"], 0) + 1
    print(f"wrote {OUT}: {len(feats)} zones {counts}")
    missing = set(KIND) - set(counts)
    if missing:
        print(f"no polygons for: {sorted(missing)}")


if __name__ == "__main__":
    main()

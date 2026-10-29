"""Download OSM roads for the coverage wards and write data/osm_roads.geojson.

Run from the repo root:  python scripts/import_osm.py   (add --refresh to re-download)

One Overpass query: the union of one bounding box per coverage ward. The raw answer
is saved in data/raw/overpass_roads.json (data only, never executed) and reused
on the next run, so we don't hit Overpass again.
A way is kept if any of its points lies in a coverage ward (so boundary roads
such as Mathura Road stay in). ward_no = ward of the way's midpoint, or of its
first point inside coverage if the midpoint is just outside.
Roads are OpenStreetMap data (ODbL).
"""
import itertools
import json
import math
import sys
from pathlib import Path

import httpx
from fetch_wards import COVERAGE, point_in_multipolygon  # same folder, reuse the helper

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "overpass_roads.json"
OUT = ROOT / "data" / "osm_roads.geojson"
# Main server first; the mirrors are only tried if it fails (it was down on 2026-10-27).
OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            "https://overpass.private.coffee/api/interpreter"]
# motorway (NH-48 expressway) down to living_street / service (plus the *_link ramps of the big classes)
HIGHWAY = "^(motorway|trunk|primary|secondary|tertiary)(_link)?$|^(unclassified|residential|living_street|service)$"


def coverage_wards():
    wards = json.loads((ROOT / "data" / "wards.geojson").read_text(encoding="utf-8"))["features"]
    return {f["properties"]["ward_no"]: f["geometry"]["coordinates"]
            for f in wards if f["properties"]["ward_no"] in COVERAGE}


def bbox(polys):
    pts = [pt for poly in polys for pt in poly[0]]
    lons, lats = [p[0] for p in pts], [p[1] for p in pts]
    return min(lats), min(lons), max(lats), max(lons)  # Overpass order: S, W, N, E


def fetch(wards):
    boxes = "".join(f'way["highway"~"{HIGHWAY}"]({s},{w},{n},{e});'
                    for s, w, n, e in (bbox(polys) for polys in wards.values()))
    return overpass(f"[out:json][timeout:300];({boxes});out tags geom;", RAW)


def overpass(query, raw):
    """Run one Overpass query; the answer is cached in `raw` and reused unless --refresh."""
    if raw.exists() and "--refresh" not in sys.argv:
        return json.loads(raw.read_text(encoding="utf-8"))
    for url in OVERPASS:
        try:
            r = httpx.post(url, data={"data": query}, timeout=400,
                           headers={"User-Agent": "civic-grievance-student-project (one-off import)"})
            r.raise_for_status()
            break
        except httpx.HTTPError as err:
            print(f"{url} failed ({err}), trying the next server")
    else:
        sys.exit("all Overpass servers failed, try again later")
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(r.text, encoding="utf-8")
    return r.json()


def midpoint(line):
    """Point halfway along the line (planar lon/lat is fine for a road segment)."""
    seg = [math.dist(a, b) for a, b in itertools.pairwise(line)]
    half = sum(seg) / 2
    for (a, b), d in zip(itertools.pairwise(line), seg):
        if half <= d and d > 0:
            t = half / d
            return a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
        half -= d
    return line[0]


def ward_of(x, y, wards):
    return next((no for no, polys in wards.items() if point_in_multipolygon(x, y, polys)), None)


def build():
    wards = coverage_wards()
    data = fetch(wards)
    features = []
    for way in data["elements"]:
        line = [[p["lon"], p["lat"]] for p in way.get("geometry", [])]
        if len(line) < 2:
            continue
        ward = ward_of(*midpoint(line), wards)
        if ward is None:  # midpoint outside: keep it only if some point is inside (boundary road)
            ward = next((w for w in (ward_of(x, y, wards) for x, y in line) if w), None)
        if ward is None:
            continue
        tags = way.get("tags", {})
        features.append({
            "type": "Feature",
            "properties": {
                "osm_way_id": way["id"],
                "name": tags.get("name", ""),
                "highway_class": tags["highway"],
                "ref": tags.get("ref", ""),  # e.g. NH48; a ref alone does NOT mean NHAI (NH-2 is still PWD)
                "width": tags.get("width", ""),
                "ward_no": ward,
            },
            "geometry": {"type": "LineString", "coordinates": line},
        })
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    named = sum(1 for f in features if f["properties"]["name"])
    print(f"wrote {OUT}: {len(features)} ways ({named} named) of {len(data['elements'])} in the ward boxes")
    for no in sorted(wards):
        print(f"  ward {no}: {sum(1 for f in features if f['properties']['ward_no'] == no)} ways")
    return features


if __name__ == "__main__":
    feats = build()
    # sanity check: the main road of the pilot is in
    assert any("mathura" in f["properties"]["name"].lower() for f in feats), "no Mathura Road found"

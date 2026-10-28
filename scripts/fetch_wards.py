"""Download the Delhi MCD 2022 ward map and convert it to data/wards.geojson.

Run from the repo root:  python scripts/fetch_wards.py

Source: OpenCity "Delhi MCD Wards Map - 2022" (KML, public domain, built from
GSDL/SEC delimitation maps). The live OpenCity file now returns 404, so we use
the Wayback Machine copy. That copy came from Common Crawl, which cuts files at
5 MiB, so only the first ~190 wards are complete (see data/COVERAGE.md).
Because the file is cut off, it is not valid XML as a whole: we pull out each
complete <Placemark>...</Placemark> and parse that piece with xml.etree.

Only the Python standard library is used. Downloads are treated as data:
saved in data/raw/, never executed.
"""
import itertools
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OPENCITY = "https://data.opencity.in/dataset/fff2ab15-7813-4198-8a97-80b0501481db/resource"
WARDS_URL = ("https://web.archive.org/web/20250622055622id_/" + OPENCITY
             + "/5e24cd4e-8ef0-4e59-9446-e74af981d370/download/delhi_wards.kml")
ZONES_URL = ("https://web.archive.org/web/20250114093144id_/" + OPENCITY
             + "/7d445bd0-8354-45f5-90a6-b97bcd6c1ecc/download/delhi-zones.kml")
WARDS_KML = RAW / "delhi_wards_2022_archive.kml"
ZONES_KML = RAW / "delhi_zones_2022_archive.kml"

# The 8 coverage wards (reasons in data/COVERAGE.md).
COVERAGE = {143, 144, 145, 180, 184, 185, 186, 187}
KALINDI_KUNJ_GHAT = (77.311, 28.544)  # (lon, lat)
GHAT_WARD = 185  # expected to contain the ghat point; checked below
EXPECTED_WARDS = 250


def download(url, path):
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "civic-grievance-student-project"})
    with urllib.request.urlopen(req, timeout=300) as r:
        path.write_bytes(r.read())


def parse_ring(text):
    """'lon,lat[,alt] lon,lat ...' -> [[lon, lat], ...]"""
    return [[float(v) for v in pt.split(",")[:2]] for pt in text.split()]


def read_placemarks(path):
    """Yield (fields dict, [polygon, ...]) for every complete Placemark.
    A polygon is [outer_ring, hole_ring, ...]."""
    text = path.read_text(encoding="utf-8")
    for chunk in re.findall(r"<Placemark>.*?</Placemark>", text, re.DOTALL):
        pm = ET.fromstring(chunk)  # the chunk has no namespace, so tags are plain
        fields = {sd.get("name"): (sd.text or "").strip() for sd in pm.iter("SimpleData")}
        polygons = []
        for poly in pm.iter("Polygon"):
            rings = [parse_ring(c.text) for c in poly.iter("coordinates")]
            polygons.append(rings)  # first <coordinates> is outerBoundaryIs
        yield fields, polygons


# ---------- small geometry helpers (planar lon/lat is fine at ward scale) ----------

def point_in_ring(x, y, ring):
    """Ray casting: count edge crossings of a ray going right from (x, y)."""
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def point_in_multipolygon(x, y, polygons):
    for outer, *holes in polygons:
        if point_in_ring(x, y, outer) and not any(point_in_ring(x, y, h) for h in holes):
            return True
    return False


def centroid(ring):
    """Area-weighted centroid of one ring (shoelace formula)."""
    a = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        cross = x1 * y2 - x2 * y1
        a += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    return cx / (3 * a), cy / (3 * a)


def area(ring):
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))) / 2


def segments_cross(p1, p2, p3, p4):
    """True if segment p1-p2 properly crosses p3-p4 (touching ends don't count)."""
    def side(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2 = side(p3, p4, p1), side(p3, p4, p2)
    d3, d4 = side(p1, p2, p3), side(p1, p2, p4)
    return d1 * d2 < 0 and d3 * d4 < 0


def ring_is_valid(ring):
    """Closed, >= 4 points, non-zero area, no self-crossing edges.
    ponytail: O(n^2) edge check, fine for a few rings; use shapely for bulk checks."""
    if len(ring) < 4 or ring[0] != ring[-1] or area(ring) == 0:
        return False
    edges = list(itertools.pairwise(ring))
    for i in range(len(edges)):
        for j in range(i + 2, len(edges)):
            if i == 0 and j == len(edges) - 1:
                continue  # first and last edge share the closing point
            if segments_cross(*edges[i], *edges[j]):
                return False
    return True


# ---------- build ----------

def build():
    download(WARDS_URL, WARDS_KML)
    download(ZONES_URL, ZONES_KML)
    retrieved = date.fromtimestamp(WARDS_KML.stat().st_mtime).isoformat()

    zones = [(f.get("WARD_2017_", "").title(), polys) for f, polys in read_placemarks(ZONES_KML)]

    features = []
    for f, polys in read_placemarks(WARDS_KML):
        ward_no = int(f.get("WNo_SEC") or 0)
        if ward_no == 0:  # one unnamed non-ward polygon (AC 44 New Delhi); skip it
            continue
        biggest = max((p[0] for p in polys), key=area)
        cx, cy = centroid(biggest)
        zone = next((name for name, zp in zones if point_in_multipolygon(cx, cy, zp)), "")
        features.append({
            "type": "Feature",
            "properties": {
                "ward_no": ward_no,
                "name_en": f.get("WardName", "").title(),
                "name_hi": "",  # not in the source
                "mcd_zone": zone,
                "in_coverage": ward_no in COVERAGE,
                "source": "OpenCity 'Delhi MCD Wards Map - 2022' (via Wayback Machine): " + WARDS_URL,
                "retrieved_at": retrieved,
            },
            "geometry": {"type": "MultiPolygon", "coordinates": polys},
        })
    features.sort(key=lambda ft: ft["properties"]["ward_no"])
    out = ROOT / "data" / "wards.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    print(f"wrote {out} with {len(features)} wards")
    return features


def check(features):
    """Sanity checks. Fails loudly on anything that would break routing."""
    by_no = {ft["properties"]["ward_no"]: ft for ft in features}
    if len(by_no) != EXPECTED_WARDS:
        missing = sorted(set(range(1, EXPECTED_WARDS + 1)) - set(by_no))
        print(f"WARNING: {len(by_no)} of {EXPECTED_WARDS} wards; missing {missing}")

    assert COVERAGE <= set(by_no), f"coverage wards missing: {COVERAGE - set(by_no)}"
    for no in sorted(COVERAGE):
        for outer, *holes in by_no[no]["geometry"]["coordinates"]:
            assert all(ring_is_valid(r) for r in [outer, *holes]), f"ward {no} has an invalid ring"
    x, y = KALINDI_KUNJ_GHAT
    hits = [no for no, ft in by_no.items() if point_in_multipolygon(x, y, ft["geometry"]["coordinates"])]
    assert hits == [GHAT_WARD], f"Kalindi Kunj ghat point falls in wards {hits}, expected [{GHAT_WARD}]"

    zones = json.loads((ROOT / "data" / "special_zones.geojson").read_text(encoding="utf-8"))
    for ft in zones["features"]:
        for outer, *holes in ft["geometry"]["coordinates"]:
            assert ring_is_valid(outer), f"special zone {ft['properties']['name']} invalid"
    ghat = next(ft for ft in zones["features"] if ft["properties"]["type"] == "ghat")
    assert point_in_multipolygon(x, y, ghat["geometry"]["coordinates"]), "ghat point outside ghat polygon"
    print(f"checks ok: ghat point in ward {GHAT_WARD}, {len(COVERAGE)} coverage wards valid")


if __name__ == "__main__":
    feats = build()
    if "--no-check" not in sys.argv:
        check(feats)

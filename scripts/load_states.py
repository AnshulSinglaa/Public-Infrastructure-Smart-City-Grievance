"""Build data/states.geojson (36 Indian states/UTs) and upsert it into the state_boundary table.

Run from the repo root:  python scripts/load_states.py          (add --build to rebuild the GeoJSON)
Uses DATABASE_URL (default: the local Docker DB). PostGIS does the simplification.

Source: Natural Earth 10m admin-1 states/provinces (public domain), India rows (adm0_a3 = IND).
It already has the post-2019 units (J&K and Ladakh separate; Dadra & Nagar Haveli and Daman & Diu
merged). Natural Earth shows de facto lines in Kashmir and the north-east, not India's official map.
Codes are moved to the current ISO 3166-2:IN codes (2023 change: CT->CG, OR->OD, TG->TS, UT->UK).
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
DB = os.environ.get("DATABASE_URL", "postgresql://postgres:dev@127.0.0.1:5433/civic")
NE_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
          "ne_10m_admin_1_states_provinces.geojson")
RAW = ROOT / "data" / "raw" / "states" / "ne_10m_admin_1.geojson"
OUT = ROOT / "data" / "states.geojson"
ISO_2023 = {"CT": "CG", "OR": "OD", "TG": "TS", "UT": "UK"}
SIMPLIFY = 0.01  # degrees, about 1 km: only used to find which state a pin is in
# (lon, lat) -> expected state code; checked after every load
CHECKS = {"Badhra, Haryana": ((75.96, 28.68), "HR"), "Noida": ((77.39, 28.57), "UP"),
          "Gurugram": ((77.03, 28.46), "HR"), "Connaught Place": ((77.2167, 28.6315), "DL")}


def build(conn):
    if not RAW.exists():
        RAW.parent.mkdir(parents=True, exist_ok=True)
        RAW.write_bytes(urllib.request.urlopen(NE_URL, timeout=600).read())
    india = [f for f in json.loads(RAW.read_text(encoding="utf-8"))["features"]
             if f["properties"]["adm0_a3"] == "IND"]
    assert len(india) == 36, f"expected 36 states/UTs, got {len(india)}"
    feats = []
    for f in india:
        code = f["properties"]["iso_3166_2"].removeprefix("IN-")
        geom = conn.execute(
            "SELECT ST_AsGeoJSON(ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SimplifyPreserveTopology("
            "ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), %s)), 3)), 5)",
            [json.dumps(f["geometry"]), SIMPLIFY]).fetchone()[0]
        feats.append({"type": "Feature", "geometry": json.loads(geom), "properties": {
            "code": ISO_2023.get(code, code), "name": f["properties"]["name"],
            "source": "Natural Earth 10m admin-1 states/provinces v5 (" + NE_URL + "), simplified ~1 km",
            "licence": "Public domain (Natural Earth)"}})
    feats.sort(key=lambda f: f["properties"]["code"])
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    print(f"wrote {OUT} with {len(feats)} states/UTs")


def main():
    with psycopg.connect(DB) as conn:
        if "--build" in sys.argv or not OUT.exists():
            build(conn)
        if conn.execute("SELECT to_regclass('state_boundary')").fetchone()[0] is None:
            sys.exit("table state_boundary does not exist yet (backend migration 0004); run again after it")
        for f in json.loads(OUT.read_text(encoding="utf-8"))["features"]:
            p = f["properties"]
            conn.execute("""
                INSERT INTO state_boundary (code, name, geom)
                VALUES (%(code)s, %(name)s, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%(geom)s), 4326)))
                ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, geom = EXCLUDED.geom""",
                {"code": p["code"], "name": p["name"], "geom": json.dumps(f["geometry"])})
        n = conn.execute("SELECT count(*) FROM state_boundary").fetchone()[0]
        print(f"state_boundary: {n} rows")
        for label, ((x, y), want) in CHECKS.items():
            got = [r[0] for r in conn.execute("SELECT code FROM state_boundary WHERE ST_Contains(geom, "
                                              "ST_SetSRID(ST_MakePoint(%s, %s), 4326))", (x, y))]
            print(f"  {label}: {got}")
            assert got == [want], f"{label} should be in {want}, got {got}"


if __name__ == "__main__":
    main()

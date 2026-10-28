"""Load data/wards.geojson (all wards) and data/special_zones.geojson into the DB.

Run from the repo root:  python scripts/load_wards.py
Uses DATABASE_URL (default: the local Docker DB from .env.example).
Safe to re-run: wards are upserted on ward_no, zones on name. We don't
TRUNCATE because reports point at ward.id / special_zone.id.
"""
import json
import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
DB = os.environ.get("DATABASE_URL", "postgresql://postgres:dev@127.0.0.1:5433/civic")
GEOM = "ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%(geom)s), 4326))"


def features(name):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))["features"]


def main():
    with psycopg.connect(DB) as conn:  # one transaction: all or nothing
        for f in features("wards.geojson"):
            p = f["properties"]
            conn.execute(f"""
                INSERT INTO ward (ward_no, name_en, name_hi, mcd_zone, geom, in_coverage, source, version)
                VALUES (%(no)s, %(name)s, %(hi)s, %(zone)s, {GEOM}, %(cov)s, %(src)s, %(ver)s)
                ON CONFLICT (ward_no) DO UPDATE SET name_en = EXCLUDED.name_en, name_hi = EXCLUDED.name_hi,
                    mcd_zone = EXCLUDED.mcd_zone, geom = EXCLUDED.geom, in_coverage = EXCLUDED.in_coverage,
                    source = EXCLUDED.source, version = EXCLUDED.version""",
                {"no": p["ward_no"], "name": p["name_en"], "hi": p["name_hi"] or None,
                 "zone": p["mcd_zone"] or None, "geom": json.dumps(f["geometry"]),
                 "cov": p["in_coverage"], "src": p["source"], "ver": p["retrieved_at"]})
        for f in features("special_zones.geojson"):
            p = f["properties"]
            args = {"type": p["type"], "name": p["name"], "prio": p["priority"],
                    "geom": json.dumps(f["geometry"])}
            cur = conn.execute(f"UPDATE special_zone SET type = %(type)s, priority = %(prio)s, geom = {GEOM} "
                               "WHERE name = %(name)s", args)
            if cur.rowcount == 0:
                conn.execute(f"INSERT INTO special_zone (type, name, priority, geom) "
                             f"VALUES (%(type)s, %(name)s, %(prio)s, {GEOM})", args)
        total, covered = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE in_coverage) FROM ward").fetchone()
        ghat = conn.execute("SELECT ward_no FROM ward WHERE ST_Contains(geom, "
                            "ST_SetSRID(ST_MakePoint(77.311, 28.544), 4326))").fetchall()
        zones = conn.execute("SELECT count(*) FROM special_zone").fetchone()[0]
    print(f"ward: {total} rows, {covered} in coverage; special_zone: {zones} rows")
    print(f"ghat point 28.544N 77.311E is in ward(s) {[r[0] for r in ghat]}")
    assert [r[0] for r in ghat] == [185], "ghat point should be in ward 185 only"


if __name__ == "__main__":
    main()

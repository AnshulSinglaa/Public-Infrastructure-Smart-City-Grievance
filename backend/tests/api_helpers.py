"""Shared test helpers (not in conftest.py: importing from conftest breaks if ui/tests
ever gets its own conftest)."""

import io

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app

client = TestClient(app)
COVERED = {"lat": "28.544", "lng": "77.311"}  # inside covered ward 185


def jpeg(size=(2000, 1500), gps=True) -> bytes:
    """A noisy JPEG, by default with camera + GPS EXIF (what a phone gallery photo carries)."""
    img = Image.effect_noise(size, 60).convert("RGB")
    exif = Image.Exif()
    exif[0x010F] = "TestCam"  # Make
    if gps:
        exif.get_ifd(0x8825).update({1: "N", 2: (28.0, 32.0, 38.4), 3: "E", 4: (77.0, 18.0, 0.0)})
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def seed_routing_data(conn) -> None:
    """Rules, departments and contacts from the CSVs, if scripts/load_rules.py hasn't loaded
    them yet. ON CONFLICT DO NOTHING: whatever the loader put there wins."""
    import csv

    from app.intake.catalog import DATA, departments

    for code, d in departments().items():
        dept_id = conn.execute(
            "INSERT INTO department (code, name_en, name_hi, level, owns, jurisdiction_type,"
            " helpline, grievance_url, email, source_url, verified)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (code) DO UPDATE SET code = EXCLUDED.code RETURNING id",
            (code, d["name_en"], d["name_hi"], d["level"], d["owns"], d["jurisdiction_type"],
             d["helpline"] or None, d["grievance_url"] or None, d["email"] or None,
             d["source_url"] or None, d["verified"]),
        ).fetchone()[0]
        for kind, value, link in (("helpline", d["helpline"], f"tel:{d['helpline']}"),
                                  ("portal", d["grievance_url"], d["grievance_url"])):
            if value:
                conn.execute("INSERT INTO contact (department_id, channel_type, label, value,"
                             " deep_link) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                             (dept_id, kind, d["name_en"], value, link))
    with (DATA / "rules_draft.csv").open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            conn.execute(
                "INSERT INTO routing_rule (rule_id, priority, categories, condition,"
                " department_code, sla_days, sla_source_url, rule_source_url, confidence, notes)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (r["rule_id"], int(r["priority"]), r["category"].split(";"), r["condition"],
                 r["department_code"], int(r["sla_days"]) if r["sla_days"] else None,
                 r["sla_source_url"] or None, r["rule_source_url"] or None,
                 float(r["confidence"]), r["notes"] or None))


TEST_WAY = -424242  # osm_way_id of test roads; removed again after the tests


def add_test_road(conn, lat, lng, way=TEST_WAY, half=0.0001, **cols) -> None:
    """A 20 m road through the pin (so it is the nearest one), e.g. highway_class='residential'.
    `way` (a negative osm_way_id) lets a test delete just its own road afterwards; `half` is
    half its length in degrees of longitude (0.0001 = ~10 m)."""
    cols = {"name": "Test road", "highway_class": "unclassified", "pwd_listed": False} | cols
    names = ", ".join(cols)
    conn.execute(
        f"INSERT INTO road_segment (osm_way_id, geom, {names}) VALUES (%s,"
        " ST_MakeLine(ST_SetSRID(ST_MakePoint(%s, %s), 4326), ST_SetSRID(ST_MakePoint(%s, %s),"
        f" 4326)), {', '.join(['%s'] * len(cols))})",
        (way, lng - half, lat, lng + half, lat, *cols.values()))


def remove_test_roads(conn, way=TEST_WAY) -> None:
    conn.execute("DELETE FROM road_segment WHERE osm_way_id = %s", (way,))

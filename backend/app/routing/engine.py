"""Routing, Sprint 2-3 version: real ward lookup from the pin, department hard-coded to MCD."""

import json
from pathlib import Path

import psycopg

from app.config import settings

POINT = "ST_SetSRID(ST_MakePoint(%s, %s), 4326)"  # ST_MakePoint takes (x, y) = (lng, lat)
# Delhi's outer edge. Source: OpenCity MCD wards 2022 (public domain), union of 250 wards,
# plus the NDMC area and Delhi Cantonment (not MCD wards; DataMeet, CC BY) so that CP and
# India Gate count as Delhi.
# ponytail: simplified to ~20 m, so a pin within ~20 m of the state border may get the other
# out-of-coverage text; both texts give the same helplines-only answer.
DELHI_OUTLINE = json.dumps(json.loads(
    (Path(__file__).parent / "delhi_outline.geojson").read_text(encoding="utf-8"))["geometry"])


def find_ward(conn: psycopg.Connection, lat: float, lng: float) -> dict | None:
    """The covered ward for a pin. ST_Covers (not ST_Contains) counts a pin exactly on a ward
    edge or corner as inside. A pin that is in no covered ward but within 15 m of one (GPS
    noise at a boundary) gets the nearest; ::geography makes the distance metres."""
    pin = f"{POINT}::geography"
    row = conn.execute(
        "SELECT id, ward_no, name_en FROM ward WHERE in_coverage"
        f" AND ST_DWithin(geom::geography, {pin}, %s)"
        f" ORDER BY ST_Covers(geom, {POINT}) DESC, ST_Distance(geom::geography, {pin}) LIMIT 1",
        (lng, lat, settings.boundary_buffer_m, lng, lat, lng, lat),
    ).fetchone()
    return {"ward_id": row[0], "ward_no": row[1], "ward_name": row[2]} if row else None


def in_delhi(conn: psycopg.Connection, lat: float, lng: float) -> bool:
    return conn.execute(
        f"SELECT ST_Covers(ST_GeomFromGeoJSON(%s), {POINT})", (DELHI_OUTLINE, lng, lat)
    ).fetchone()[0]


def route(conn: psycopg.Connection, category: str, lat: float, lng: float):
    """The routing answer, or None when the pin is out of coverage (ARCH §4 step 1:
    no ward, or a ward we don't cover yet -> make no department claim)."""
    ward = find_ward(conn, lat, lng)
    if ward is None:
        return None
    return {
        **ward,
        "department_code": "MCD",
        "confidence": 0.9,
        "confidence_label": "very_likely",
        "curated": True,
        "fake": True,  # the department is hard-coded until the real rules land
        "sla_days": 15,
    }


def general_channels(conn: psycopg.Connection) -> list[dict]:
    """City-wide helplines (contact.scope = 'all') for reports we can't route."""
    rows = conn.execute(
        "SELECT channel_type, label, value, deep_link FROM contact WHERE scope = 'all' ORDER BY id"
    ).fetchall()
    return [{"type": r[0], "label": r[1], "value": r[2], "deep_link": r[3]} for r in rows]

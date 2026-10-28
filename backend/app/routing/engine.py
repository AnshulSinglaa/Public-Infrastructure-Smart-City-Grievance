"""Routing, Sprint 2 version: hard-coded MCD, plus a real ward lookup when a pin is given."""

import psycopg

# S2 fake, used ONLY when the report has no pin (the UI sends none yet). Not a real lookup.
NO_PIN_WARD = {"ward_id": None, "ward_no": 185, "ward_name": "Madanpur Khadar East"}


def find_ward(conn: psycopg.Connection, lat: float, lng: float) -> dict | None:
    """Point-in-polygon, covered wards only. ST_MakePoint takes (x, y) = (lng, lat)."""
    row = conn.execute(
        "SELECT id, ward_no, name_en FROM ward WHERE in_coverage"
        " AND ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326)) LIMIT 1",
        (lng, lat),
    ).fetchone()
    return {"ward_id": row[0], "ward_no": row[1], "ward_name": row[2]} if row else None


def route(conn: psycopg.Connection, category: str, lat: float | None, lng: float | None):
    """The routing answer, or None when the pin is out of coverage (ARCH §4 step 1:
    no ward, or a ward we don't cover yet -> make no department claim)."""
    if lat is None or lng is None:
        ward = NO_PIN_WARD
    else:
        ward = find_ward(conn, lat, lng)
        if ward is None:
            return None
    return {
        **ward,
        "department_code": "MCD",
        "confidence": 0.9,
        "confidence_label": "very_likely",
        "curated": True,
        "fake": True,  # S2: the department is hard-coded, and with no pin so is the ward
        "sla_days": 15,
    }


def general_channels(conn: psycopg.Connection) -> list[dict]:
    """City-wide helplines (contact.scope = 'all') for reports we can't route."""
    rows = conn.execute(
        "SELECT channel_type, label, value, deep_link FROM contact WHERE scope = 'all' ORDER BY id"
    ).fetchall()
    return [{"type": r[0], "label": r[1], "value": r[2], "deep_link": r[3]} for r in rows]

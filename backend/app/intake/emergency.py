"""The 'call first' card for dangerous issues. It never blocks the report."""

from app.intake.catalog import categories, departments

# Categories flagged emergency=yes in categories.csv -> what kind of danger it is.
CATEGORY_KIND = {"LIVE_WIRE": "live_wire", "TRANSFORMER": "live_wire", "GAS_LEAK": "gas_leak",
                 "MANHOLE_OPEN": "open_manhole", "TREE_FALLEN": "fallen_tree",
                 "DANGEROUS_BUILDING": "building_collapse", "ROAD_CAVE_IN": "road_cave_in"}
KIND_DEPARTMENT = {"fire": "DFS", "gas_leak": "IGL"}  # live_wire: the discom for the pin
# When the pin's discom is unknown (no discom_area zone there), all three, by area (B-036).
DISCOMS = {"BRPL": "South & West Delhi", "BYPL": "East & Central Delhi",
           "TPDDL": "North & North-West Delhi"}


def emergency_kind(image: dict | None, text: dict | None, category: str | None) -> str | None:
    """Either model saying 'emergency', or the category's own emergency flag."""
    for a in (image, text):
        if a and a.get("emergency"):
            return a.get("emergency_kind") or "other"
    if category and categories().get(category, {}).get("emergency") == "yes":
        return CATEGORY_KIND.get(category, "other")
    return None


def phone(code: str, label: str | None = None) -> dict | None:
    dept = departments().get(code)
    if not dept or not dept["helpline"]:
        return None
    number = dept["helpline"]
    return {"label": label or dept["name_en"], "value": number, "deep_link": f"tel:{number}"}


def discom_for(conn, lat: float, lng: float) -> str | None:
    """Which power company's area the pin is in (special_zone 'discom_area' lookup layer;
    the zone name starts with the company code, e.g. 'BRPL (coverage wards)')."""
    row = conn.execute(
        "SELECT name FROM special_zone WHERE type = 'discom_area'"
        " AND ST_Covers(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326)) LIMIT 1",
        (lng, lat),
    ).fetchone()
    return row[0].split()[0] if row else None


def call_first(conn, kind: str, lat: float, lng: float) -> dict:
    """112 always, plus the helpline for this kind of danger when we know it."""
    numbers = [{"label": "Emergency 112", "value": "112", "deep_link": "tel:112"}]
    if kind == "live_wire":
        code = discom_for(conn, lat, lng)
        extras = ([phone(code)] if code else
                  [phone(c, f"{departments()[c]['name_en']} ({area})") for c, area in DISCOMS.items()])
    else:
        code = KIND_DEPARTMENT.get(kind)
        extras = [phone(code)] if code else []
    numbers += [e for e in extras if e]
    return {"kind": kind, "call_first": numbers}

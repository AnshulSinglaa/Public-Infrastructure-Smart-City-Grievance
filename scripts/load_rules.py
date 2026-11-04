"""Load routing rules, departments + contacts and OSM road segments into the DB.

Run from the repo root:  python scripts/load_rules.py   (after match_pwd.py; needs migration 0005)
Uses DATABASE_URL (default: the local Docker DB). Safe to re-run: everything is upserted.

- departments.csv -> department (upsert on code) + contact (helpline / portal / email rows,
  upsert on department + channel + value; contacts no longer in the CSV are removed).
- rules_draft.csv -> routing_rule (upsert on rule_id; rules no longer in the CSV are removed).
- osm_roads.geojson -> road_segment (upsert on osm_way_id). pwd_listed / pwd_road_id / row_m
  come only from pwd_matches.csv rows with confirmed = auto, auto_route or yes. A way with several
  confirmed stretches gets the lowest road_id and the smallest RoW. Ring Road route-relation
  members are all PWD-listed (row_source = route_relation).
- dataset_version: sha256 of the input files; one row per distinct hash, stamped on the rules.
"""
import csv
import hashlib
import json
import os
import re
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB = os.environ.get("DATABASE_URL", "postgresql://postgres:dev@127.0.0.1:5433/civic")
FILES = ["rules_draft.csv", "departments.csv", "osm_roads.geojson", "pwd_list.csv", "pwd_matches.csv"]
CONFIRMED = {"auto", "auto_route", "yes"}
RING_ROUTE = DATA / "ring_road_relations.json"  # written by match_pwd.py; committed (OSM, ODbL)
RING_ROAD_RELATION = 1208531  # Outer Ring Road (1208532) is not done: its PWD rows leave gaps


def read(name):
    with open(DATA / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def none(v):
    return v.strip() or None if isinstance(v, str) else v


def dataset_hash():
    h = hashlib.sha256()
    for name in FILES:
        h.update((DATA / name).read_bytes())
    return h.hexdigest()


def pwd_by_way():
    """osm_way_id -> (pwd_road_id, row_m) from the confirmed matches."""
    row_m = {r["road_id"]: float(r["row_m"]) if r["row_m"] else None for r in read("pwd_list.csv")}
    out = {}
    for m in read("pwd_matches.csv"):
        if m["confirmed"] in CONFIRMED:
            out.setdefault(int(m["osm_way_id"]), []).append(m["pwd_road_id"])
    return {way: (min(ids), min((row_m[i] for i in ids if row_m.get(i)), default=None))
            for way, ids in out.items()}


def deep_link(channel, value):
    """What a button opens (B-052): tel:<digits> for a helpline, the URL for a portal."""
    if channel == "helpline":
        return "tel:" + re.sub(r"\D", "", value)
    return f"mailto:{value}" if channel == "email" else value


def load_departments(conn):
    depts = read("departments.csv")
    for d in depts:
        dept_id = conn.execute("""
            INSERT INTO department (code, name_en, name_hi, level, owns, jurisdiction_type, helpline,
                                    grievance_url, email, source_url, accessed, verified)
            VALUES (%(code)s, %(name_en)s, %(name_hi)s, %(level)s, %(owns)s, %(jurisdiction_type)s, %(helpline)s,
                    %(grievance_url)s, %(email)s, %(source_url)s, %(accessed)s, %(verified)s)
            ON CONFLICT (code) DO UPDATE SET name_en = EXCLUDED.name_en, name_hi = EXCLUDED.name_hi,
                level = EXCLUDED.level, owns = EXCLUDED.owns, jurisdiction_type = EXCLUDED.jurisdiction_type,
                helpline = EXCLUDED.helpline, grievance_url = EXCLUDED.grievance_url, email = EXCLUDED.email,
                source_url = EXCLUDED.source_url, accessed = EXCLUDED.accessed, verified = EXCLUDED.verified
            RETURNING id""", {k: none(v) or ("" if k == "name_hi" else None) for k, v in d.items()}).fetchone()[0]
        contacts = [(ch, d[col]) for ch, col in (("helpline", "helpline"), ("portal", "grievance_url"),
                                                  ("email", "email")) if d[col].strip()]
        for channel, value in contacts:
            conn.execute("""
                INSERT INTO contact (department_id, channel_type, label, value, deep_link, last_verified,
                                     source_url)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (department_id, channel_type, value) DO UPDATE
                SET label = EXCLUDED.label, deep_link = EXCLUDED.deep_link,
                    last_verified = EXCLUDED.last_verified, source_url = EXCLUDED.source_url""",
                (dept_id, channel, d["name_en"], value.strip(), deep_link(channel, value.strip()),
                 none(d["accessed"]), none(d["source_url"])))
        conn.execute("DELETE FROM contact WHERE department_id = %s AND channel_type || '|' || value <> ALL(%s)",
                     (dept_id, [f"{ch}|{v.strip()}" for ch, v in contacts]))
    return len(depts)


def load_rules(conn, version_id):
    rules = read("rules_draft.csv")
    for r in rules:
        conn.execute("""
            INSERT INTO routing_rule (rule_id, priority, categories, condition, department_code, sla_days,
                                      sla_source_url, rule_source_url, confidence, notes, dataset_version, last_verified)
            VALUES (%(rule_id)s, %(priority)s, %(categories)s, %(condition)s, %(department_code)s, %(sla_days)s,
                    %(sla_source_url)s, %(rule_source_url)s, %(confidence)s, %(notes)s, %(v)s, %(last_verified)s)
            ON CONFLICT (rule_id) DO UPDATE SET priority = EXCLUDED.priority, categories = EXCLUDED.categories,
                condition = EXCLUDED.condition, department_code = EXCLUDED.department_code,
                sla_days = EXCLUDED.sla_days, sla_source_url = EXCLUDED.sla_source_url,
                rule_source_url = EXCLUDED.rule_source_url, confidence = EXCLUDED.confidence,
                notes = EXCLUDED.notes, dataset_version = EXCLUDED.dataset_version,
                last_verified = EXCLUDED.last_verified""",
            {**{k: none(v) for k, v in r.items()}, "priority": int(r["priority"]),
             "categories": [c.strip() for c in r["category"].split(";") if c.strip()] or ["*"],
             "sla_days": int(r["sla_days"]) if r["sla_days"].strip() else None,
             "confidence": float(r["confidence"]), "v": version_id})  # last_verified: CSV, empty = unknown
    conn.execute("DELETE FROM routing_rule WHERE rule_id <> ALL(%s)", ([r["rule_id"] for r in rules],))
    return len(rules)


def load_roads(conn):
    pwd = pwd_by_way()
    feats = json.loads((DATA / "osm_roads.geojson").read_text(encoding="utf-8"))["features"]
    conn.execute("CREATE TEMP TABLE r (osm_way_id bigint, name text, highway_class text, ref text, ward_no int, "
                 "pwd_listed boolean, pwd_road_id text, row_m real, geom text) ON COMMIT DROP")
    with conn.cursor().copy("COPY r FROM STDIN") as cp:
        for f in feats:
            p = f["properties"]
            road_id, row_m = pwd.get(p["osm_way_id"], (None, None))
            cp.write_row((p["osm_way_id"], p["name"] or None, p["highway_class"], p.get("ref") or None, p["ward_no"],
                          road_id is not None, road_id, row_m, json.dumps(f["geometry"])))
    cols = "name, highway_class, ref, ward_no, pwd_listed, pwd_road_id, row_m, row_source, geom"
    vals = ("r.name, r.highway_class, r.ref, r.ward_no, r.pwd_listed, r.pwd_road_id, r.row_m, "
            "CASE WHEN r.pwd_listed THEN 'pwd_list' END, ST_SetSRID(ST_GeomFromGeoJSON(r.geom), 4326)")
    conn.execute(f"UPDATE road_segment s SET ({cols}) = ({vals}) FROM r WHERE s.osm_way_id = r.osm_way_id")
    conn.execute(f"INSERT INTO road_segment (osm_way_id, {cols}) SELECT r.osm_way_id, {vals} FROM r "
                 "WHERE NOT EXISTS (SELECT 1 FROM road_segment s WHERE s.osm_way_id = r.osm_way_id)")
    # Every way of the OSM Ring Road route relation is PWD (flyovers, underpasses, both carriageways),
    # name match or not. It takes the stretch id and RoW of the nearest name-confirmed Ring Road way.
    members = [m["ref"] for e in json.loads(RING_ROUTE.read_text(encoding="utf-8"))["elements"]
               if e["id"] == RING_ROAD_RELATION for m in e["members"] if m["type"] == "way"]
    conn.execute("""
        UPDATE road_segment s SET pwd_listed = true, row_source = 'route_relation',
               pwd_road_id = n.pwd_road_id, row_m = n.row_m
        FROM road_segment s2 CROSS JOIN LATERAL (
            SELECT c.pwd_road_id, c.row_m FROM road_segment c
            WHERE c.osm_way_id = ANY(%(m)s) AND c.row_source = 'pwd_list'
            ORDER BY c.geom::geography <-> s2.geom::geography LIMIT 1) n
        WHERE s.id = s2.id AND s.osm_way_id = ANY(%(m)s) AND NOT s.pwd_listed""", {"m": members})
    return len(feats), conn.execute("SELECT count(*) FROM road_segment WHERE pwd_listed").fetchone()[0]


def main():
    with psycopg.connect(DB) as conn:  # one transaction: all or nothing
        sha = dataset_hash()
        row = conn.execute("SELECT id FROM dataset_version WHERE sha256 = %s", (sha,)).fetchone()
        version_id = row[0] if row else conn.execute(
            "INSERT INTO dataset_version (sha256) VALUES (%s) RETURNING id", (sha,)).fetchone()[0]
        n_dept = load_departments(conn)
        n_rules = load_rules(conn, version_id)
        n_roads, n_pwd = load_roads(conn)
        n_contacts = conn.execute("SELECT count(*) FROM contact").fetchone()[0]
        unknown = conn.execute("SELECT array_agg(rule_id) FROM routing_rule r WHERE NOT EXISTS "
                               "(SELECT 1 FROM department d WHERE d.code = r.department_code)").fetchone()[0]
    print(f"dataset_version {version_id} ({sha[:12]}): {n_dept} departments, {n_contacts} contacts, "
          f"{n_rules} rules, {n_roads} road segments ({n_pwd} PWD-listed)")
    assert not unknown, f"rules with unknown department: {unknown}"


if __name__ == "__main__":
    main()

"""Sanity-check data/rules_draft.csv against the other data files (no DB needed).

Run from the repo root:  python scripts/check_rules.py     (exit code 1 if anything is broken)
Checks: every department_code is in departments.csv, every category is in categories.csv
('*' = any), and every zone named in a condition ("special_zone X", "discom_area X") exists
in special_zones.geojson (as a type, or as a discom_area department_code).
"""
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def read(name):
    with open(DATA / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def problems():
    rules = read("rules_draft.csv")
    depts = {r["code"] for r in read("departments.csv")}
    cats = {r["code"] for r in read("categories.csv")} | {"*"}
    zones = json.loads((DATA / "special_zones.geojson").read_text(encoding="utf-8"))["features"]
    ztypes = {f["properties"]["type"] for f in zones}
    discoms = {f["properties"].get("department_code") for f in zones if f["properties"]["type"] == "discom_area"}
    out = []
    for r in rules:
        rid = r["rule_id"]
        if r["department_code"] not in depts:
            out.append(f"{rid}: department_code {r['department_code']!r} not in departments.csv")
        for c in filter(None, (c.strip() for c in r["category"].split(";"))):
            if c not in cats:
                out.append(f"{rid}: category {c!r} not in categories.csv")
        for z in re.findall(r"special_zone (\w+)", r["condition"]):
            if z not in ztypes:
                out.append(f"{rid}: zone type {z!r} not in special_zones.geojson")
        for d in re.findall(r"discom_area (\w+)", r["condition"]):
            if d not in discoms:
                out.append(f"{rid}: discom_area {d!r} not in special_zones.geojson")
    return len(rules), out


if __name__ == "__main__":
    n, bad = problems()
    for line in bad:
        print(line)
    print(f"{n} rules checked, {len(bad)} problems")
    sys.exit(1 if bad else 0)

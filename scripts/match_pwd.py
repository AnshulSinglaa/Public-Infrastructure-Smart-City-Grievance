"""Propose matches between the PWD road list and the OSM roads in the coverage wards.

Run from the repo root:  python scripts/match_pwd.py   (after import_osm.py)

For every named OSM way we score each PWD road by normalised-name similarity
(difflib ratio). Every PWD road scoring >= 0.85 becomes a proposal in
data/pwd_matches.csv with `confirmed` left empty: the curator types yes/no.
PWD names are often descriptive ("Mathura Road DPS to Ashram Chowk"), so we
compare the curator's `match_name` if set, else the name cut before
" from " / " to " / "," / "(".
One way can get several proposals (e.g. three PWD rows are stretches of
Mathura Road): the name alone can't tell them apart, the curator picks.

Auto-confirm (confirmed=auto) only when ALL hold: score 1.0, exactly one PWD row has
that name, the way is in a coverage ward, and the name is not generic (GENERIC_NAMES).
Exception: STRETCH_ROADS are one named road split into several PWD rows (Ring Road
etc.); every stretch proposal is auto-confirmed, since any stretch means PWD.
A proposal whose PWD division is on the opposite side of Delhi from the ward (e.g. a North West
division road matched to a way in Vasant Kunj) is set to confirmed=rejected: same name, other road.
confirmed=auto_route: the way is a member of the OSM Ring Road / Outer Ring Road route relation
and the PWD row is a Ring Road / Outer Ring Road stretch (Ring Road ways are named "Mahatma Gandhi
Marg", which is blocklisted as generic).
A curator's own yes/no in the existing pwd_matches.csv is kept on re-runs.
"""
import csv
import json
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from fetch_wards import COVERAGE, area, centroid
from import_osm import overpass

ROOT = Path(__file__).resolve().parent.parent
THRESHOLD = 0.85
GENERIC = {"marg", "road", "rd", "path"}  # words that only say "this is a road"
# Names (normalised) shared by many unrelated roads, never auto-confirmed. "mahatma gandhi" is
# also Ring Road's OSM name, but other MG roads exist, so the curator confirms it by hand.
GENERIC_NAMES = {"mahatma gandhi", "gurudwara", "main", "link", "service", "mandir", "shiv mandir", "gopal mandir",
                 "mata mandir", "hospital", "church", "idgah", "power house", "central", "mall", "main bazar",
                 "naya bazar", "shani bazar", "arya samaj", "sadar thana", "school", "station", "market",
                 "internal", "approach", "pusta", "kotla"}
GENERIC_PATTERN = re.compile(r"\bgali\b|\bno \d|\bblock\b|\bpocket\b|\bsector\b")
# One road split into several PWD rows (stretches): any stretch means PWD.
STRETCH_ROADS = {"ring", "outer ring", "mathura", "lala lajpat rai", "rani jhansi flyover", "rohtak", "new rohtak",
                 "maharaja agarsen", "narela bawana"}


def norm(name):
    """'M.P. Road' -> 'mp', 'Sri Aurobindo Marg' -> 'sri aurobindo'."""
    s = re.sub(r"[.']", "", name.lower())  # M.P. -> mp (no stray spaces)
    words = re.sub(r"[^a-z0-9]+", " ", s).split()
    return " ".join(w for w in words if w not in GENERIC)


def pwd_key(row):
    if row["match_name"]:
        return norm(row["match_name"])
    return norm(re.split(r" from | to |,|\(", row["road_name"], maxsplit=1, flags=re.IGNORECASE)[0])


def matches(osm_name, pwd_rows):
    """[(score, pwd_row), ...] for every PWD road at or above the threshold."""
    a = norm(osm_name)
    scored = ((SequenceMatcher(None, a, pwd_key(r)).ratio(), r) for r in pwd_rows)
    return [(s, r) for s, r in scored if s >= THRESHOLD]


def sides(division):
    """'North West Road Division 1' -> {'north', 'west'}; project/central divisions -> set()."""
    d = (division or "").lower().replace("shahadra", "east")
    return {w for w in ("north", "south", "east", "west") if d.startswith(w) or f" {w}" in d.split(" road")[0]}


def ward_sides():
    """Coarse side of Delhi per ward from its centroid.
    ponytail: compass box around (28.63N, 77.20E); a real check would need PWD road geometry."""
    out = {}
    for f in json.loads((ROOT / "data" / "wards.geojson").read_text(encoding="utf-8"))["features"]:
        x, y = centroid(max((p[0] for p in f["geometry"]["coordinates"]), key=area))
        ns = {"north"} if y > 28.66 else {"south"} if y < 28.60 else set()
        ew = {"east"} if x > 77.25 else {"west"} if x < 77.13 else set()
        out[f["properties"]["ward_no"]] = ns | ew
    return out


OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}
# OSM route relations: Ring Road Delhi (1208531), Delhi Outer Ring Road (1208532)
ROUTES = {"ring road": 1208531, "outer ring": 1208532}


def route_members():
    """PWD-name needle -> member way ids of its OSM route relation (fetched once, cached)."""
    data = overpass("[out:json][timeout:120];relation(id:1208531,1208532);out body;",
                    ROOT / "data" / "raw" / "ring_road_relations.json")
    by_id = {e["id"]: {m["ref"] for m in e["members"] if m["type"] == "way"} for e in data["elements"]}
    return {needle: by_id.get(rel, set()) for needle, rel in ROUTES.items()}


def curate(proposals, pwd):
    """Fill `confirmed`: keep the curator's earlier yes/no, else 'auto' when the rules above hold."""
    out = ROOT / "data" / "pwd_matches.csv"
    manual = {}
    if out.exists():
        with open(out, encoding="utf-8") as f:
            manual = {(r["osm_way_id"], r["pwd_road_id"]): r["confirmed"] for r in csv.DictReader(f)
                      if r["confirmed"] in ("yes", "no")}
    rows_per_name = Counter(pwd_key(r) for r in pwd)
    api = ROOT / "data" / "raw" / "pwd" / "roads_with_pwd_api.json"
    division = {r["asset_code"]: (r.get("location") or {}).get("division")
                for r in json.loads(api.read_text(encoding="utf-8"))} if api.exists() else {}
    wsides = ward_sides()
    route = route_members()
    for p in proposals:
        key = norm(p["osm_name"])
        unique = rows_per_name[key] == 1 or key in STRETCH_ROADS
        auto = (p["score"] == 1.0 and unique and p["ward_no"] in COVERAGE
                and key not in GENERIC_NAMES and not GENERIC_PATTERN.search(key))
        far = any(OPPOSITE[s] in wsides[p["ward_no"]] for s in sides(division.get(p["pwd_road_id"])))
        # Ring Road ways are named "Mahatma Gandhi Marg" (blocklisted): confirm only members of the route relation
        on_route = any(p["osm_way_id"] in ways and needle in p["pwd_name"].lower()
                       and ("outer" in p["pwd_name"].lower()) == (needle == "outer ring")
                       for needle, ways in route.items())
        status = "rejected" if far else "auto" if auto else "auto_route" if on_route else ""
        p["confirmed"] = manual.get((str(p["osm_way_id"]), p["pwd_road_id"]), status)


def main():
    roads = json.loads((ROOT / "data" / "osm_roads.geojson").read_text(encoding="utf-8"))["features"]
    with open(ROOT / "data" / "pwd_list.csv", encoding="utf-8") as f:
        pwd = list(csv.DictReader(f))

    proposals = []
    for ft in roads:
        p = ft["properties"]
        if not norm(p["name"]):
            continue
        for score, row in matches(p["name"], pwd):
            proposals.append({"osm_way_id": p["osm_way_id"], "osm_name": p["name"], "pwd_name": row["road_name"],
                              "score": round(score, 2), "confirmed": "",
                              "pwd_road_id": row["road_id"], "ward_no": p["ward_no"]})
    proposals.sort(key=lambda r: (r["pwd_name"], r["ward_no"], r["osm_way_id"]))
    curate(proposals, pwd)

    with open(ROOT / "data" / "pwd_matches.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(proposals[0]))
        w.writeheader()
        w.writerows(proposals)

    matched = Counter(r["pwd_name"] for r in proposals)
    ways = len({r["osm_way_id"] for r in proposals})
    print(f"{len(proposals)} proposals on {ways} OSM ways for {len(matched)} of {len(pwd)} PWD roads "
          f"({sum(1 for f in roads if f['properties']['name'])} named OSM ways checked)")
    c = Counter(r["confirmed"] or "unconfirmed" for r in proposals)
    print(f"confirmed: {dict(c)}; auto on {len({r['osm_way_id'] for r in proposals if r['confirmed'] == 'auto'})} ways")
    unmatched = sum(1 for r in pwd if r["road_name"] not in matched)
    print(f"{unmatched} PWD roads with no proposal (most are outside the coverage wards)")
    return proposals


if __name__ == "__main__":
    props = main()
    # sanity check: Mathura Road in Madanpur Khadar West / Sarita Vihar is proposed
    hits = {r["ward_no"] for r in props if "mathura" in r["osm_name"].lower()}
    assert {186, 187} & hits, f"no Mathura Road proposal in ward 186/187 (got wards {sorted(hits)})"
    print(f"check ok: Mathura Road proposals in wards {sorted(hits)}")

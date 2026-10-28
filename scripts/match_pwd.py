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
"""
import csv
import json
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THRESHOLD = 0.85
GENERIC = {"marg", "road", "rd", "path"}  # words that only say "this is a road"


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

    with open(ROOT / "data" / "pwd_matches.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(proposals[0]))
        w.writeheader()
        w.writerows(proposals)

    matched = Counter(r["pwd_name"] for r in proposals)
    ways = len({r["osm_way_id"] for r in proposals})
    print(f"{len(proposals)} proposals on {ways} OSM ways for {len(matched)} of {len(pwd)} PWD roads "
          f"({sum(1 for f in roads if f['properties']['name'])} named OSM ways checked)")
    for name, n in matched.most_common():
        print(f"  {n:3}  {name}")
    unmatched = [r["road_name"] for r in pwd if r["road_name"] not in matched]
    print(f"{len(unmatched)} PWD roads with no proposal (curator matches by hand):")
    for name in unmatched:
        print(f"   -  {name}")
    return proposals


if __name__ == "__main__":
    props = main()
    # sanity check: Mathura Road in Madanpur Khadar West / Sarita Vihar is proposed
    hits = {r["ward_no"] for r in props if "mathura" in r["osm_name"].lower()}
    assert {186, 187} & hits, f"no Mathura Road proposal in ward 186/187 (got wards {sorted(hits)})"
    print(f"check ok: Mathura Road proposals in wards {sorted(hits)}")

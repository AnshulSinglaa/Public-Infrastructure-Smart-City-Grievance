"""Download PWD Delhi's "Roads with PWD" inventory and add every road to data/pwd_list.csv.

Run from the repo root:  python scripts/fetch_pwd.py   (add --refresh to re-download)

The inventory page (https://www.pwddelhi.gov.in/maintenance-inventory/roads-with-pwd) fills
its table from a JSON endpoint, which we page through. The raw answer is saved in
data/raw/pwd/ (data only). Rows already in pwd_list.csv (matched by road_id) are left as
they are, so the curator's match_name values survive; new rows get an empty match_name.
Every listed road is PWD whatever its width; row_ge_60ft (18.29 m) is only informational.
"""
import csv
import json
import sys
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PAGE = "https://www.pwddelhi.gov.in/maintenance-inventory/roads-with-pwd"
API = "https://www.pwddelhi.gov.in/api/maintenance/roads/search"
RAW = ROOT / "data" / "raw" / "pwd" / "roads_with_pwd_api.json"
CSV = ROOT / "data" / "pwd_list.csv"
LIMIT = 100


def download():
    if RAW.exists() and "--refresh" not in sys.argv:
        return json.loads(RAW.read_text(encoding="utf-8"))
    headers = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest", "Referer": PAGE,
               "User-Agent": "civic-grievance-student-project (one-off import)"}
    roads, page = [], 1
    while True:
        r = httpx.get(API, params={"page": page, "limit": LIMIT, "sort_by": "created_at"},
                      headers=headers, timeout=60)
        r.raise_for_status()
        batch = r.json()["data"]["roads"]
        roads += [dict(rd, page=page) for rd in batch]
        if len(batch) < LIMIT:
            break
        page += 1
    RAW.write_text(json.dumps(roads, ensure_ascii=False), encoding="utf-8")
    return roads


def main():
    roads = download()
    with open(CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    fields = list(rows[0])
    have = {r["road_id"] for r in rows}
    today = date.fromtimestamp(RAW.stat().st_mtime).isoformat()
    added = 0
    for rd in roads:
        if rd["asset_code"] in have or rd.get("status") != "active":
            continue
        row_m = rd.get("right_of_way_m")
        rows.append({
            "road_name": rd["road_name"].strip(), "source_url": PAGE, "page": rd["page"], "retrieved_at": today,
            "road_id": rd["asset_code"], "start_point": (rd.get("starting_point") or "").strip(),
            "end_point": (rd.get("end_point") or "").strip(),
            "row_m": f"{row_m:.2f}" if row_m else "", "row_ge_60ft": ("yes" if row_m >= 18.29 else "no") if row_m else "",
            "match_name": "",
            "source_quality": f"official: PWD Delhi 'Roads with PWD' inventory JSON ({API}, {len(roads)} roads, "
                              f"read {today}). Being listed means PWD, whatever the width",
        })
        have.add(rd["asset_code"])
        added += 1
    with open(CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(roads)} roads in the inventory; added {added}; pwd_list.csv now has {len(rows)} rows")


if __name__ == "__main__":
    main()

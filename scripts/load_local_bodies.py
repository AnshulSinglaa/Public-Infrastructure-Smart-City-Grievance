"""Load India's urban local bodies from the official Local Government Directory (LGD) into
the local_body table (FIX-PLAN-S6 C). Safe to re-run (upsert on the LGD code).

lgdirectory.gov.in puts its downloads behind a CAPTCHA, so the file is downloaded by hand:
  1. Open https://lgdirectory.gov.in/downloadDirectory.do in a browser.
  2. Under "Local body (urban)", choose "All Urban Local Bodies of India" (all states).
  3. Format: CSV. Type the CAPTCHA, click Download.
  4. Save it as data/lgd/urban_local_bodies.csv (or pass its path as the first argument).
Then:  uv run python scripts/load_local_bodies.py [path.csv]

The LGD file has a title line or two above the header and its column names vary a little
between exports, so columns are found by name ("Local Body Code", "Local Body Name (In English)",
"Local Body Type Name", "District Name", "State Name").
"""
import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.db import connect
from app.rag.local_body import norm_name, state_code

DEFAULT = Path(__file__).resolve().parent.parent / "data" / "lgd" / "urban_local_bodies.csv"


def pick(header: list[str], *need: str, avoid: tuple[str, ...] = ()) -> int | None:
    """Index of the first column whose name has every word in `need` and none in `avoid`."""
    for i, h in enumerate(header):
        h = h.lower()
        if all(n in h for n in need) and not any(a in h for a in avoid):
            return i
    return None


def read_rows(text: str) -> list[dict]:
    rows = list(csv.reader(io.StringIO(text)))
    start = next((i for i, r in enumerate(rows)
                  if any("local body code" in c.lower() for c in r)), None)
    if start is None:
        raise SystemExit("No 'Local Body Code' column found: is this the LGD urban local body CSV?")
    h = rows[start]
    col = {"code": pick(h, "local body code"),
           "name": pick(h, "local body name", "english") or pick(h, "local body name",
                                                                  avoid=("local)", "type")),
           "name_local": pick(h, "local body name", "local)"),
           "type": pick(h, "type", "name") or pick(h, "type", avoid=("code",)),
           "district": pick(h, "district name") or pick(h, "district", avoid=("code",)),
           "state": pick(h, "state name") or pick(h, "state", avoid=("code",))}
    missing = [k for k in ("code", "name", "type", "state") if col[k] is None]
    if missing:
        raise SystemExit(f"LGD columns not found: {missing}. Header: {h}")
    out = []
    for r in rows[start + 1:]:
        def get(k, r=r):
            return r[col[k]].strip() if col[k] is not None and col[k] < len(r) else ""

        if not get("code").isdigit() or not get("name"):
            continue  # footer / blank lines
        out.append({"lgd_code": int(get("code")), "name": get("name"),
                    "name_local": get("name_local") or None, "body_type": get("type"),
                    "district": get("district") or None, "state_name": get("state"),
                    "state_code": state_code(get("state")), "name_norm": norm_name(get("name"))})
    return out


UPSERT = """
INSERT INTO local_body (lgd_code, name, name_local, body_type, district, state_name, state_code, name_norm)
VALUES (%(lgd_code)s, %(name)s, %(name_local)s, %(body_type)s, %(district)s, %(state_name)s,
        %(state_code)s, %(name_norm)s)
ON CONFLICT (lgd_code) DO UPDATE SET name = EXCLUDED.name, name_local = EXCLUDED.name_local,
    body_type = EXCLUDED.body_type, district = EXCLUDED.district, state_name = EXCLUDED.state_name,
    state_code = EXCLUDED.state_code, name_norm = EXCLUDED.name_norm
"""


def load(conn, rows: list[dict]) -> int:
    with conn.cursor() as cur:
        cur.executemany(UPSERT, rows)
    return len(rows)


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if not path.exists():
        raise SystemExit(f"{path} not found. Download it first (steps at the top of this file).")
    rows = read_rows(path.read_bytes().decode("utf-8-sig", "replace"))
    with connect(timeout=10) as conn:
        n = load(conn, rows)
    unknown = sorted({r["state_name"] for r in rows if not r["state_code"]})
    print(f"loaded {n} urban local bodies" + (f"; no state code for: {unknown}" if unknown else ""))


if __name__ == "__main__":
    main()

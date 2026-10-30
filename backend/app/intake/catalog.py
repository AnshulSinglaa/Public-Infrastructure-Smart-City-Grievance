"""The curated CSVs in data/: the 37 issue categories and the departments' helplines."""

import csv
from functools import cache

from app.config import ROOT

DATA = ROOT / "data"


def read_csv(name: str, key: str) -> dict[str, dict]:
    path = DATA / name
    if not path.exists():  # e.g. state_directory.csv before the data agent adds it
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {row[key]: row for row in csv.DictReader(f)}


@cache
def categories() -> dict[str, dict]:
    """Code -> row. The `code` column (UPPERCASE, e.g. 'POTHOLE') is the only list of
    category codes; every part of the app uses these codes exactly as written."""
    return read_csv("categories.csv", "code")


@cache
def departments() -> dict[str, dict]:
    return read_csv("departments.csv", "code")


@cache
def states() -> dict[str, dict]:
    return read_csv("state_directory.csv", "state_code")

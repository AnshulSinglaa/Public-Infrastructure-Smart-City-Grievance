"""Who is the local body here? Town name or GPS -> the urban local body from the official Local
Government Directory (LGD, table local_body, scripts/load_local_bodies.py). FIX-PLAN-S6 C.

    find(conn, "Badhra", "HR") -> {"name": "Badhra", "body_type": "Municipal Committee",
                                   "district": "Charkhi Dadri", "state_code": "HR", ...}

It answers "usually your local body handles this": a general rule, never a cited fact.
"""

import csv
import re
import unicodedata
from difflib import SequenceMatcher
from functools import cache

import httpx

from app.config import ROOT
from app.routing.engine import find_state

# Words that name the kind of body, not the place: "Municipal Committee Badhra" -> "badhra".
TYPE_WORDS = re.compile(
    r"\b(municipal|corporation|council|committee|nagar|nigam|palika|parishad|panchayat|"
    r"mahanagar|town|area|notified|cantonment|board|city|industrial|township|m\.?c\.?|"
    r"np|npp|nnp|mcl|mcorp|of|the)\b", re.IGNORECASE)
MIN_SCORE = 0.85
NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"
HEADERS = {"User-Agent": "Civic Grievance Router (student project)"}  # Nominatim policy


def norm_name(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    name = TYPE_WORDS.sub(" ", re.sub(r"[^a-z0-9 .]", " ", name))
    return re.sub(r"\s+", " ", name.replace(".", " ")).strip()


@cache
def state_codes() -> dict[str, str]:
    """state name (normalised) -> state_directory.csv code, plus LGD spellings."""
    with open(ROOT / "data" / "state_directory.csv", encoding="utf-8") as f:
        out = {norm_state(r["state_name"]): r["state_code"] for r in csv.DictReader(f)}
    out |= {"andaman and nicobar islands": "AN", "the dadra and nagar haveli and daman and diu": "DH",
            "nct of delhi": "DL", "orissa": "OD", "pondicherry": "PY"}
    return out


def norm_state(name: str) -> str:
    return re.sub(r"\s+", " ", name.lower().replace("&", "and")).strip()


def state_code(state_name: str) -> str | None:
    return state_codes().get(norm_state(state_name))


def find(conn, place: str | None = None, state: str | None = None, lat: float | None = None,
         lng: float | None = None, reverse=None) -> dict | None:
    """The best-matching urban local body, or None. `state` is a state code; with GPS the state
    comes from our state outlines and the town name from OSM (Nominatim reverse) if not given."""
    if lat is not None and lng is not None:
        if not state and (row := find_state(conn, lat, lng)):
            state = row[0]
        if not place:
            place = (reverse or reverse_geocode)(lat, lng)
    if not place:
        return None
    key = norm_name(place)
    if not key:
        return None
    sql = ("SELECT lgd_code, name, body_type, district, state_name, state_code, name_norm "
           "FROM local_body")
    rows = conn.execute(sql + (" WHERE state_code = %s" if state else ""),
                        (state,) if state else ()).fetchall()
    best, best_score = None, 0.0
    for r in rows:
        score = 1.0 if r[6] == key else SequenceMatcher(None, key, r[6]).ratio()
        if score > best_score or (score == best_score and best and rank(r[2]) < rank(best[2])):
            best, best_score = r, score
    if not best or best_score < MIN_SCORE:
        return None
    code, name, body_type, district, state_name, sc, _ = best
    return {"lgd_code": code, "name": name, "body_type": body_type, "district": district,
            "state_name": state_name, "state_code": sc, "score": round(best_score, 2),
            "label": f"{body_type} {name}, {district}, {state_name}" if district
            else f"{body_type} {name}, {state_name}"}


def rank(body_type: str) -> int:
    """Same name twice (a town and its corporation)? Prefer the bigger body."""
    t = body_type.lower()
    return 0 if "corporation" in t else 1 if "council" in t else 2


def reverse_geocode(lat: float, lng: float) -> str | None:
    """Town / city / village name for a pin from OSM. None when unknown or offline."""
    try:
        r = httpx.get(NOMINATIM_REVERSE, headers=HEADERS, timeout=5, params={
            "lat": lat, "lon": lng, "format": "jsonv2", "zoom": 14, "accept-language": "en"})
        r.raise_for_status()
        a = r.json().get("address", {})
    except (httpx.HTTPError, ValueError):
        return None
    return next((a[k] for k in ("city", "town", "municipality", "village", "suburb") if a.get(k)),
                None)

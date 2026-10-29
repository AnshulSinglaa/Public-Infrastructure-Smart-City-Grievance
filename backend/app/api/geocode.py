"""POST /geocode: landmark text -> up to 5 map candidates inside Delhi (Nominatim / OSM)."""

from functools import lru_cache

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.reports import User, error

router = APIRouter(prefix="/api/v1")

NOMINATIM = "https://nominatim.openstreetmap.org/search"
DELHI_VIEWBOX = "76.84,28.88,77.35,28.40"  # left lng, top lat, right lng, bottom lat
# Nominatim's usage policy requires an identifying User-Agent.
HEADERS = {"User-Agent": "Civic Grievance Router (student project)"}


class GeocodeIn(BaseModel):
    text: str = Field(min_length=1, max_length=200)


# ponytail: in-memory cache per process, lost on restart; also no 1 request/s throttle
# (Nominatim policy). Fine for a demo; a shared cache + throttle if traffic grows.
@lru_cache(maxsize=1000)
def search(text: str) -> tuple:
    r = httpx.get(NOMINATIM, headers=HEADERS, timeout=5, params={
        "q": text, "format": "jsonv2", "limit": 5, "viewbox": DELHI_VIEWBOX, "bounded": 1,
        "countrycodes": "in",
    })
    r.raise_for_status()
    return tuple({"lat": float(p["lat"]), "lng": float(p["lon"]), "label": p["display_name"],
                  "source": "nominatim"} for p in r.json()[:5])


@router.post("/geocode")
def geocode(body: GeocodeIn, user_id: User):  # login required: no open Nominatim proxy
    text = body.text.strip()
    try:
        found = search(text)
        # "sarkari school ke saamne, Jaitpur": Nominatim can't read the Hinglish part, but
        # the place name after the last comma often works. One retry, no more.
        tail = text.rsplit(",", 1)[-1].strip()
        if not found and tail and tail != text:
            found = search(tail)
        return {"candidates": list(found)}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):  # network, bad JSON, odd shape
        return error(502, "geocode_failed", "geocode_failed")

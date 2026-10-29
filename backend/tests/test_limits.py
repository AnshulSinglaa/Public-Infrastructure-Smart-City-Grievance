import uuid

import httpx
import pytest
from api_helpers import COVERED, client
from test_health import db_is_up

from app import auth
from app.api import geocode
from app.db import connect

needs_db = pytest.mark.skipif(not db_is_up(), reason="database not reachable")


def post(headers, **data):
    return client.post("/api/v1/reports", headers=headers, data={"text": "gaddha", **data})


def user_id(headers):
    return auth.read_token(headers["Authorization"].removeprefix("Bearer "))


@needs_db
def test_same_spot_limit(headers):
    assert post(headers, **COVERED).status_code == 201
    r = post(headers, lat=28.5441, lng=77.3111)  # ~15 m away
    assert r.status_code == 429
    assert r.json()["error"]["message_key"] == "rate_limited_same_spot"
    assert post(headers, lat=28.546, lng=77.311).status_code == 201  # ~220 m away is fine


@needs_db
def test_daily_limit(headers):
    with connect() as conn:  # 10 earlier reports today
        conn.execute("INSERT INTO report (user_id) SELECT %s FROM generate_series(1, 10)",
                     (user_id(headers),))
    r = post(headers, **COVERED)
    assert r.status_code == 429
    assert r.json()["error"]["message_key"] == "rate_limited_daily"


@needs_db
def test_cooldown_after_three_junk_photos(headers):
    with connect() as conn:
        conn.execute("INSERT INTO report (user_id, junk_reason)"
                     " SELECT %s, 'blurry' FROM generate_series(1, 3)", (user_id(headers),))
    r = post(headers, **COVERED)
    assert r.status_code == 429
    assert r.json()["error"]["message_key"] == "rate_limited_cooldown"


H = {"Authorization": f"Bearer {auth.make_token(uuid.uuid4())}"}


def test_geocode_needs_a_token():
    r = client.post("/api/v1/geocode", json={"text": "Jaitpur"})
    assert r.status_code == 401
    assert r.json()["error"]["message_key"] == "login_required"


class FakeResponse:
    def __init__(self, places):
        self.places = places

    def raise_for_status(self):
        pass

    def json(self):
        return self.places


def test_geocode_maps_and_caches(monkeypatch):
    geocode.search.cache_clear()
    calls = []
    place = {"lat": "28.50", "lon": "77.32", "display_name": "Govt School, Jaitpur"}

    def fake_get(url, **kwargs):
        calls.append(kwargs)
        return FakeResponse([place] * 7)

    monkeypatch.setattr(geocode.httpx, "get", fake_get)
    for _ in range(2):
        r = client.post("/api/v1/geocode", headers=H, json={"text": "sarkari school, Jaitpur"})
        assert r.status_code == 200
        assert r.json()["candidates"] == [
            {"lat": 28.5, "lng": 77.32, "label": "Govt School, Jaitpur", "source": "nominatim"}] * 5
    assert len(calls) == 1  # second call came from the cache
    assert calls[0]["headers"]["User-Agent"].startswith("Civic Grievance Router")
    assert calls[0]["params"]["bounded"] == 1 and calls[0]["timeout"] == 5


def test_geocode_error_is_502(monkeypatch):
    geocode.search.cache_clear()

    def down(url, **kwargs):
        raise httpx.ConnectTimeout("slow")

    monkeypatch.setattr(geocode.httpx, "get", down)
    r = client.post("/api/v1/geocode", headers=H, json={"text": "Jaitpur"})
    assert r.status_code == 502
    assert r.json()["error"]["message_key"] == "geocode_failed"
    assert client.post("/api/v1/geocode", headers=H, json={"text": "x" * 201}).status_code == 422


def test_geocode_retries_with_the_place_after_the_last_comma(monkeypatch):  # B-019
    geocode.search.cache_clear()
    asked = []
    place = {"lat": "28.50", "lon": "77.32", "display_name": "Jaitpur, Delhi"}

    def fake_get(url, **kwargs):
        asked.append(kwargs["params"]["q"])
        return FakeResponse([place] if kwargs["params"]["q"] == "Jaitpur" else [])

    monkeypatch.setattr(geocode.httpx, "get", fake_get)
    r = client.post("/api/v1/geocode", headers=H, json={"text": "sarkari school ke saamne, Jaitpur"})
    assert r.json()["candidates"][0]["label"] == "Jaitpur, Delhi"
    assert asked == ["sarkari school ke saamne, Jaitpur", "Jaitpur"]
    asked.clear()
    client.post("/api/v1/geocode", headers=H, json={"text": "nowhere"})
    assert asked == ["nowhere"]  # no comma: no retry

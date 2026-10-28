import pytest
from fastapi.testclient import TestClient
from test_health import db_is_up

from app.config import settings
from app.main import app

client = TestClient(app)
needs_db = pytest.mark.skipif(not db_is_up(), reason="database not reachable")

# Smallest valid PNG header is enough: we only sniff the first bytes.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


def test_needs_photo_or_text():
    r = client.post("/api/v1/reports", data={"text": "   "})
    assert r.status_code == 422
    assert r.json() == {"error": {"code": "validation", "message_key": "need_photo_or_text"}}


def test_rejects_non_image_photo():
    r = client.post("/api/v1/reports", files={"photo": ("x.png", b"not an image", "image/png")})
    assert r.status_code == 422
    assert r.json()["error"]["message_key"] == "photo_not_jpeg_png"


def test_rejects_two_photos():
    files = [("photo", ("a.png", PNG, "image/png")), ("photo", ("b.png", PNG, "image/png"))]
    r = client.post("/api/v1/reports", files=files)
    assert r.status_code == 422
    assert r.json()["error"]["message_key"] == "too_many_photos"


ZERO_UUID = "00000000-0000-0000-0000-000000000000"
CASES = [  # (method, url, request kwargs, status, message_key)
    ("post", "/api/v1/reports", {"data": {"text": "x" * 1001}}, 422, "text_too_long"),
    ("post", "/api/v1/reports", {"data": {"text": "x", "lat": "28.5"}}, 422, "need_lat_and_lng"),
    ("post", "/api/v1/reports",
     {"files": {"photo": ("p.png", PNG.ljust(5 * 1024 * 1024 + 1, b"0"))}},  # 5 MB + 1
     422, "photo_too_large"),
    ("post", "/api/v1/reports", {"data": {"text": "x", "lat": "91", "lng": "77"}},
     422, "invalid_input"),
    pytest.param("get", f"/api/v1/media/{ZERO_UUID}", {}, 404, "media_not_found", marks=needs_db),
    ("get", "/api/v1/nope", {}, 404, "not_found"),
    ("delete", "/healthz", {}, 405, "server_error"),
]


@pytest.mark.parametrize("method, url, kwargs, status, key", CASES)
def test_errors_use_error_shape(method, url, kwargs, status, key):
    r = client.request(method.upper(), url, **kwargs)
    assert r.status_code == status
    assert r.json()["error"]["message_key"] == key
    if key == "invalid_input":
        assert r.json()["error"]["detail"]["fields"] == ["body.lat"]


def test_unhandled_error_is_500_without_details(monkeypatch):
    def broken_db():
        raise RuntimeError("password=secret")

    monkeypatch.setattr("app.api.reports.connect", broken_db)
    r = TestClient(app, raise_server_exceptions=False).post("/api/v1/reports", data={"text": "x"})
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "server_error", "message_key": "server_error"}}


@needs_db
@pytest.mark.parametrize("lat, lng", [(19.07, 72.87), (0, 0), (28.7335, 77.0746)])
def test_pin_out_of_coverage_makes_no_claim(lat, lng):
    # Mumbai, the ocean, and ward 27 (a Delhi ward we don't cover yet).
    r = client.post("/api/v1/reports", data={"text": "gaddha", "lat": lat, "lng": lng})
    assert r.status_code == 201
    body = r.json()
    assert body["state"] == "out_of_coverage"
    assert body["master_id"] is None and body["dossier_id"] is None
    assert body["next"]["kind"] == "general_channels"
    assert body["next"]["channels"][0]["value"] == "155305"


@needs_db
def test_text_only_report_gives_dossier():
    r = client.post("/api/v1/reports", data={"text": "bada gaddha", "ui_lang": "hi"})
    assert r.status_code == 201
    body = r.json()
    assert body["state"] == "dossier_ready" and body["next"] is None and body["master_id"]
    assert body["notices"] == ["fake_ai"]

    d = client.get(f"/api/v1/dossiers/{body['dossier_id']}").json()
    assert d["category"] == "pothole" and d["severity"] == "high"
    assert d["department"]["code"] == "MCD"
    assert d["pin"]["ward_no"] == 185  # no pin -> hard-coded ward
    assert d["channels"][0]["value"] == "155305"
    assert "bada gaddha" in d["complaint"]["hi"] and "bada gaddha" in d["complaint"]["en"]
    assert d["photo_url"] is None


@needs_db
def test_photo_is_stored_and_served(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path)
    r = client.post(
        "/api/v1/reports",
        files={"photo": ("p.png", PNG, "image/png")},
        data={"lat": "28.544", "lng": "77.311"},
    )
    assert r.status_code == 201
    d = client.get(f"/api/v1/dossiers/{r.json()['dossier_id']}").json()
    assert d["pin"]["lat"] == 28.544 and d["pin"]["ward_no"] == 185  # real lookup, covered

    m = client.get(d["photo_url"])
    assert m.status_code == 200
    assert m.headers["content-type"] == "image/png"
    assert m.content == PNG
    assert len(list(tmp_path.iterdir())) == 1


@needs_db
def test_unknown_dossier_is_404():
    r = client.get("/api/v1/dossiers/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404

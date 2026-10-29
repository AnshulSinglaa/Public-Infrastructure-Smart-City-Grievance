import pytest
from api_helpers import COVERED, client, jpeg
from fastapi.testclient import TestClient
from test_health import db_is_up

from app.config import settings
from app.main import app

needs_db = pytest.mark.skipif(not db_is_up(), reason="database not reachable")

PNG_HEADER_ONLY = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # right magic bytes, not an image


def post(headers, **kwargs):
    return client.post("/api/v1/reports", headers=headers, **kwargs)


def test_needs_photo_or_text(token_headers):
    r = post(token_headers, data={"text": "   ", **COVERED})
    assert r.status_code == 422
    assert r.json() == {"error": {"code": "validation", "message_key": "need_photo_or_text"}}


@pytest.mark.parametrize("photo", [b"not an image", PNG_HEADER_ONLY])
def test_rejects_non_image_photo(token_headers, photo):
    r = post(token_headers, files={"photo": ("x.png", photo, "image/png")}, data=COVERED)
    assert r.status_code == 422
    assert r.json()["error"]["message_key"] == "photo_not_jpeg_png"


def test_rejects_two_photos(token_headers):
    files = [("photo", ("a.jpg", jpeg(), "image/jpeg")), ("photo", ("b.jpg", jpeg(), "image/jpeg"))]
    r = post(token_headers, files=files, data=COVERED)
    assert r.status_code == 422
    assert r.json()["error"]["message_key"] == "too_many_photos"


@pytest.mark.parametrize("data", [{"text": "x"}, {"text": "x", "lat": "28.5"}])
def test_missing_pin_is_422(token_headers, data):
    r = post(token_headers, data=data)
    assert r.status_code == 422
    assert r.json()["error"]["message_key"] == "need_pin"


ZERO_UUID = "00000000-0000-0000-0000-000000000000"
CASES = [  # (method, url, request kwargs, status, message_key)
    ("post", "/api/v1/reports", {"data": {"text": "x" * 1001, **COVERED}}, 422, "text_too_long"),
    ("post", "/api/v1/reports",
     {"files": {"photo": ("p.png", PNG_HEADER_ONLY.ljust(5 * 1024 * 1024 + 1, b"0"))},
      "data": COVERED},  # 5 MB + 1
     422, "photo_too_large"),
    ("post", "/api/v1/reports", {"data": {"text": "x", "lat": "91", "lng": "77"}},
     422, "invalid_input"),
    ("post", "/api/v1/reports", {"data": {"text": "x", "accuracy_m": "inf", **COVERED}},
     422, "invalid_input"),
    pytest.param("get", f"/api/v1/media/{ZERO_UUID}", {}, 404, "media_not_found", marks=needs_db),
    ("get", "/api/v1/nope", {}, 404, "not_found"),
    ("delete", "/healthz", {}, 405, "server_error"),
]


@pytest.mark.parametrize("method, url, kwargs, status, key", CASES)
def test_errors_use_error_shape(token_headers, method, url, kwargs, status, key):
    r = client.request(method.upper(), url, headers=token_headers, **kwargs)
    assert r.status_code == status
    assert r.json()["error"]["message_key"] == key
    if key == "invalid_input":
        assert len(r.json()["error"]["detail"]["fields"]) == 1


def test_unhandled_error_is_500_without_details(monkeypatch, token_headers):
    def broken_db():
        raise RuntimeError("password=secret")

    monkeypatch.setattr("app.api.reports.connect", broken_db)
    r = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/reports", data={"text": "x", **COVERED}, headers=token_headers)
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "server_error", "message_key": "server_error"}}


@needs_db
@pytest.mark.parametrize("lat, lng, in_delhi", [
    (19.07, 72.87, False), (0, 0, False), (28.7335, 77.0746, True)])
def test_pin_out_of_coverage_makes_no_claim(headers, lat, lng, in_delhi):
    # Mumbai, the ocean, and ward 27 (a Delhi ward we don't cover yet).
    r = post(headers, data={"text": "gaddha", "lat": lat, "lng": lng})
    assert r.status_code == 201
    body = r.json()
    assert body["state"] == "out_of_coverage"
    assert body["master_id"] is None and body["dossier_id"] is None
    assert body["next"]["kind"] == "general_channels"
    assert body["next"]["outside_delhi"] is not in_delhi
    assert [c["value"] for c in body["next"]["channels"]] == (["155305"] if in_delhi else [])


@needs_db
def test_text_report_gives_masked_dossier_and_encrypted_text(headers):
    text = "सड़क पर बड़ा गड्ढा hai, call 98765 43210"
    r = post(headers, data={"text": text, "accuracy_m": "80", "pin_source": "gps", **COVERED})
    assert r.status_code == 201
    body = r.json()
    assert body["state"] == "dossier_ready" and body["next"] is None and body["master_id"]
    assert body["notices"] == ["fake_ai", "low_gps_accuracy"]

    d = client.get(f"/api/v1/dossiers/{body['dossier_id']}", headers=headers).json()
    assert d["category"] == "pothole" and d["department"]["code"] == "MCD"
    assert d["pin"]["ward_no"] == 185
    assert d["channels"][0]["value"] == "155305"
    # English letter only; the citizen's Hindi words are kept as written, masked.
    assert list(d["complaint"]) == ["en"]
    assert d["citizen_text"] == "सड़क पर बड़ा गड्ढा hai, call 98XXXXXX10"
    assert f'"{d["citizen_text"]}"' in d["complaint"]["en"]
    assert "43210" not in d["complaint"]["en"]
    assert d["photo_url"] is None and d["photo_withheld"] is False

    from app.db import connect
    from app.privacy.text import decrypt
    with connect() as conn:
        stored = conn.execute("SELECT text FROM report WHERE id = %s",
                              (body["report_id"],)).fetchone()[0]
    assert "गड्ढा".encode() not in bytes(stored) and decrypt(stored) == text


@needs_db
def test_gallery_photo_gets_unverified_notice(headers, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path)
    r = post(headers, files={"photo": ("p.jpg", jpeg(), "image/jpeg")},
             data={"location_verified": "false", **COVERED})
    assert r.status_code == 201
    assert "photo_unverified_location" in r.json()["notices"]


@needs_db
def test_photo_is_cleaned_blurred_and_served(headers, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path)
    blurred = []
    monkeypatch.setattr("app.privacy.photo.find_boxes", lambda img: blurred.append(1) or [])
    r = post(headers, files={"photo": ("p.jpg", jpeg(), "image/jpeg")}, data=COVERED)
    assert r.status_code == 201
    assert r.json()["notices"] == ["fake_ai", "photo_unverified_location"]
    assert blurred == [1]  # the blur step ran
    d = client.get(f"/api/v1/dossiers/{r.json()['dossier_id']}", headers=headers).json()
    assert d["photo_withheld"] is False

    shared = client.get(d["photo_url"], headers=headers)
    original = client.get(d["photo_url"] + "?original=1", headers=headers)
    for m in (shared, original):
        assert m.status_code == 200 and m.headers["content-type"] == "image/jpeg"
        assert b"Exif" not in m.content and b"TestCam" not in m.content
    assert sorted(p.name.endswith("_blur.jpg") for p in tmp_path.iterdir()) == [False, True]


@needs_db
def test_blur_failure_withholds_photo(headers, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path)

    def broken(img):
        raise RuntimeError("cascade broke")

    monkeypatch.setattr("app.privacy.photo.find_boxes", broken)
    r = post(headers, files={"photo": ("p.jpg", jpeg(), "image/jpeg")},
             data={"location_verified": "true", **COVERED})
    assert r.status_code == 201
    assert r.json()["notices"] == ["fake_ai", "location_reported_by_phone"]  # B-013
    d = client.get(f"/api/v1/dossiers/{r.json()['dossier_id']}", headers=headers).json()
    assert d["photo_url"] is None and d["photo_withheld"] is True

    from app.db import connect
    with connect() as conn:
        media_id, status = conn.execute(
            "SELECT m.id, m.blur_status FROM report r JOIN media m ON m.id = r.media_id"
            " WHERE r.id = %s", (r.json()["report_id"],)).fetchone()
    assert status == "failed"
    assert client.get(f"/api/v1/media/{media_id}", headers=headers).status_code == 404
    # the owner can still see their own original
    assert client.get(f"/api/v1/media/{media_id}?original=1", headers=headers).status_code == 200


@needs_db
def test_other_user_gets_404(headers, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path)
    monkeypatch.setattr("app.privacy.photo.find_boxes", lambda img: [])
    r = post(headers, files={"photo": ("p.jpg", jpeg(), "image/jpeg")}, data=COVERED)
    d = client.get(f"/api/v1/dossiers/{r.json()['dossier_id']}", headers=headers).json()

    other = {"Authorization": "Bearer " + client.post(
        "/api/v1/auth/dev-login", json={"name": "Someone Else"}).json()["token"]}
    assert client.get(f"/api/v1/dossiers/{d['id']}", headers=other).status_code == 404
    for url in (d["photo_url"], d["photo_url"] + "?original=1"):
        assert client.get(url, headers=other).status_code == 404
    mine = client.get("/api/v1/me/reports", headers=other).json()["reports"]
    assert r.json()["report_id"] not in [x["report_id"] for x in mine]


@needs_db
def test_my_reports_newest_first(headers):
    first = post(headers, data={"text": "gaddha", **COVERED}).json()
    second = post(headers, data={"text": "gaddha", "lat": 19.07, "lng": 72.87}).json()
    reports = client.get("/api/v1/me/reports", headers=headers).json()["reports"]
    assert [x["report_id"] for x in reports] == [second["report_id"], first["report_id"]]
    assert reports[0] | {"created_at": None} == {
        "report_id": second["report_id"], "state": "out_of_coverage", "category": "pothole",
        "ward_no": None, "department_code": None, "created_at": None, "dossier_id": None}
    assert reports[1]["ward_no"] == 185 and reports[1]["department_code"] == "MCD"
    assert reports[1]["dossier_id"] == first["dossier_id"]


@needs_db
def test_unknown_dossier_is_404(headers):
    r = client.get(f"/api/v1/dossiers/{ZERO_UUID}", headers=headers)
    assert r.status_code == 404


def test_zero_width_text_is_not_text(token_headers):  # B-020
    r = post(token_headers, data={"text": "\u200b\u200b\u200b \u200d", **COVERED})
    assert r.status_code == 422
    assert r.json()["error"]["message_key"] == "need_photo_or_text"


def ward_edge_points():
    """A corner shared by wards 185 and 186 (from PostGIS), and the middle of one edge of 185."""
    from app.db import connect
    with connect() as conn:
        edge = conn.execute(
            "SELECT ST_Y(p), ST_X(p) FROM (SELECT ST_LineInterpolatePoint(ST_MakeLine("
            " ST_PointN(r, 1), ST_PointN(r, 2)), 0.5) p FROM (SELECT ST_ExteriorRing("
            " ST_GeometryN(geom, 1)) r FROM ward WHERE ward_no = 185) x) y").fetchone()
    return [(28.5112908, 77.3124494), edge]


@needs_db
def test_pin_on_ward_edge_or_corner_is_covered(headers):  # B-014
    for i, (lat, lng) in enumerate(ward_edge_points()):
        r = post(headers, data={"text": f"gaddha {i}", "lat": lat, "lng": lng})
        assert r.status_code == 201 and r.json()["state"] == "dossier_ready", (lat, lng)
        d = client.get(f"/api/v1/dossiers/{r.json()['dossier_id']}", headers=headers).json()
        assert d["pin"]["ward_no"] in (185, 186)


@needs_db
def test_pin_just_outside_a_covered_ward_uses_the_buffer():  # B-014: <= 15 m snaps, 40 m doesn't
    from app.db import connect
    from app.routing.engine import find_ward
    with connect() as conn:
        # The westernmost corner of all covered wards, then 10 m and 40 m further west
        # (1e-4 degree of longitude is about 9.8 m here).
        ward_no, lat, lng = conn.execute(
            "SELECT ward_no, ST_Y(p), ST_X(p) FROM ward, (SELECT p FROM (SELECT (ST_DumpPoints("
            " ST_Union(geom))).geom p FROM ward WHERE in_coverage) a ORDER BY ST_X(p) LIMIT 1) b"
            " WHERE in_coverage AND ST_Covers(geom, p) LIMIT 1").fetchone()
        near, far = find_ward(conn, lat, lng - 1e-4), find_ward(conn, lat, lng - 4e-4)
    assert near and near["ward_no"] == ward_no
    assert far is None


@needs_db
def test_location_claim_never_changes_routing(headers):  # B-013
    answers = []
    for claim, lat in (("true", "28.544"), ("false", "28.5425")):  # 167 m apart, both ward 185
        r = post(headers, files={"photo": ("p.jpg", jpeg(), "image/jpeg")},
                 data={"location_verified": claim, "lat": lat, "lng": "77.311"})
        assert r.status_code == 201
        d = client.get(f"/api/v1/dossiers/{r.json()['dossier_id']}", headers=headers).json()
        answers.append((d["department"], d["routing"], d["pin"]["ward_no"], d["deadline"]))
        note = "location_reported_by_phone" if claim == "true" else "photo_unverified_location"
        assert note in r.json()["notices"]  # never "verified"
    assert answers[0] == answers[1]


@needs_db
def test_outside_delhi_says_so_and_offers_no_delhi_helpline(headers):  # B-018
    noida = post(headers, data={"text": "gaddha", "lat": 28.535, "lng": 77.340}).json()
    assert noida["state"] == "out_of_coverage"
    assert noida["next"] == {"kind": "general_channels", "outside_delhi": True, "channels": []}
    ward27 = post(headers, data={"text": "gaddha", "lat": 28.7335, "lng": 77.0746}).json()
    assert ward27["next"]["outside_delhi"] is False and ward27["next"]["channels"]


@needs_db
def test_parallel_posts_at_one_spot_make_one_report(headers):  # B-015
    from concurrent.futures import ThreadPoolExecutor

    def one(_):
        return TestClient(app).post("/api/v1/reports", headers=headers,
                                    data={"text": "gaddha", **COVERED}).status_code

    with ThreadPoolExecutor(5) as pool:
        codes = sorted(pool.map(one, range(5)))
    assert codes == [201, 429, 429, 429, 429]


def test_every_response_has_nosniff():  # B-023
    for r in (client.get("/healthz"), client.get("/api/v1/nope")):
        assert r.headers["x-content-type-options"] == "nosniff"


def test_api_docs_only_on_local(monkeypatch):  # B-024
    import importlib

    from app import main
    assert client.get("/openapi.json").status_code == 200  # tests run with APP_ENV=local
    monkeypatch.setattr(settings, "app_env", "production")
    try:
        prod = TestClient(importlib.reload(main).app)
        for url in ("/docs", "/redoc", "/openapi.json"):
            assert prod.get(url).status_code == 404
    finally:
        monkeypatch.undo()
        importlib.reload(main)

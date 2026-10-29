import uuid

import pytest
from api_helpers import client
from test_health import db_is_up

from app import auth
from app.config import settings
from app.privacy.text import fernet

needs_db = pytest.mark.skipif(not db_is_up(), reason="database not reachable")
UNAUTHORIZED = {"error": {"code": "unauthorized", "message_key": "login_required"}}


@needs_db
def test_dev_login_same_email_same_user():
    a = client.post("/api/v1/auth/dev-login", json={"name": "Priya", "email": "p@example.com"})
    b = client.post("/api/v1/auth/dev-login", json={"name": "Priya K", "email": "P@example.com"})
    assert a.status_code == b.status_code == 200
    assert a.json()["user"]["id"] == b.json()["user"]["id"]
    assert b.json()["user"]["name"] == "Priya K"
    assert auth.read_token(a.json()["token"]) == uuid.UUID(a.json()["user"]["id"])


def test_dev_login_validates_body():
    r = client.post("/api/v1/auth/dev-login", json={"name": "", "email": "not-an-email"})
    assert r.status_code == 422


@pytest.mark.parametrize("header", [None, "Bearer junk", "Bearer a.1.b", "Token x"])
@pytest.mark.parametrize("url", ["/api/v1/me/reports", f"/api/v1/dossiers/{uuid.uuid4()}",
                                 f"/api/v1/media/{uuid.uuid4()}"])
def test_protected_routes_need_a_token(url, header):
    r = client.get(url, headers={"Authorization": header} if header else {})
    assert r.status_code == 401 and r.json() == UNAUTHORIZED


def test_post_report_needs_a_token():
    r = client.post("/api/v1/reports", data={"text": "x", "lat": 28.5, "lng": 77.3})
    assert r.status_code == 401 and r.json() == UNAUTHORIZED


def test_tampered_or_expired_token_is_rejected(monkeypatch):
    token = auth.make_token(uuid.uuid4())
    user_id, expiry, sig = token.split(".")
    assert auth.read_token(f"{uuid.uuid4()}.{expiry}.{sig}") is None  # someone else's id
    assert auth.read_token(f"{user_id}.{int(expiry) + 999}.{sig}") is None  # longer life
    monkeypatch.setattr(settings, "dev_token_days", -1)
    assert auth.read_token(auth.make_token(uuid.uuid4())) is None


def test_refuses_to_start_unsafely(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(RuntimeError, match="AUTH_MODE=dev"):
        auth.check_auth_mode()
    monkeypatch.setattr(settings, "auth_mode", "supabase")
    auth.check_auth_mode()  # fine
    fernet.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="PII_KEY"):
            fernet()
    finally:
        fernet.cache_clear()


@pytest.mark.parametrize("token", [f"{uuid.uuid4()}.123.é", "a.².b", "ü.1.x"])
def test_non_ascii_token_is_401_not_500(token):
    assert auth.read_token(token) is None
    r = client.get("/api/v1/me/reports", headers={"Authorization": f"Bearer {token}".encode()})
    assert r.status_code == 401 and r.json() == UNAUTHORIZED


def test_login_rate_limit_per_ip(monkeypatch):
    monkeypatch.setattr(auth, "connect", lambda: pytest.fail("limit must stop it first"))
    auth.login_times["testclient"].extend([auth.time.monotonic()] * auth.LOGIN_LIMIT)
    r = client.post("/api/v1/auth/dev-login", json={"name": "Spam"})
    assert r.status_code == 429
    assert r.json() == {"error": {"code": "rate_limited", "message_key": "rate_limited_login"}}
    assert not auth.login_limited("10.0.0.9")  # other IPs are not affected


def test_login_window_slides(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: clock[0])
    assert not any(auth.login_limited("1.2.3.4") for _ in range(auth.LOGIN_LIMIT))
    assert auth.login_limited("1.2.3.4")  # 11th inside 10 min
    clock[0] += auth.LOGIN_WINDOW_S + 1
    assert not auth.login_limited("1.2.3.4")  # old ones expired


def test_500_never_returns_exception_text(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    def boom(*a, **k):
        raise RuntimeError("SECRET password=hunter2 at /etc/x")

    monkeypatch.setattr(auth, "connect", boom)
    r = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/auth/dev-login", json={"name": "x"})
    assert r.status_code == 500 and "hunter2" not in r.text and "RuntimeError" not in r.text


@needs_db
def test_me_checks_a_kept_token_without_using_the_login_limit(headers):  # B-012
    for _ in range(auth.LOGIN_LIMIT + 5):
        r = client.get("/api/v1/me", headers=headers)
        assert r.status_code == 200 and r.json()["user"]["name"].startswith("test-")
    assert sum(len(t) for t in auth.login_times.values()) == 1  # only the fixture's login
    assert client.get("/api/v1/me", headers={"Authorization": "Bearer junk"}).status_code == 401

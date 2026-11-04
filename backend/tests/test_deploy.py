"""Deploy mini-sprint: Supabase login, Turnstile, prod settings, security headers.

No network: Supabase and Cloudflare are replaced by small fakes. Turnstile uses only
Cloudflare's documented test keys (always-pass / always-fail).
"""

import time
import uuid
from types import SimpleNamespace

import httpx
import jwt
import pytest
from api_helpers import client
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import SecretStr
from test_health import db_is_up

from app import auth
from app.config import Settings, prod_problems, settings

needs_db = pytest.mark.skipif(not db_is_up(), reason="database not reachable")
PROJECT = "https://test-project.supabase.co"
KEY = ec.generate_private_key(ec.SECP256R1())  # stands in for Supabase's signing key
PASS_SECRET = "1x0000000000000000000000000000000AA"  # Cloudflare test secret: always passes
FAIL_SECRET = "2x0000000000000000000000000000000AA"  # Cloudflare test secret: always fails
TEST_TOKEN = "XXXX.DUMMY.TOKEN.XXXX"  # what the test site key's widget hands out


def supabase_jwt(sub=None, key=KEY, alg="ES256", **claims) -> str:
    body = {"sub": str(sub or uuid.uuid4()), "aud": "authenticated",
            "iss": f"{PROJECT}/auth/v1", "exp": int(time.time()) + 3600, **claims}
    return jwt.encode(body, key, algorithm=alg)


@pytest.fixture
def supabase_mode(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "supabase")
    monkeypatch.setattr(settings, "supabase_url", PROJECT)
    monkeypatch.setattr(settings, "turnstile_secret", SecretStr(PASS_SECRET))
    fake_jwks = SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=KEY.public_key()))
    monkeypatch.setattr(auth, "jwks", lambda: fake_jwks)
    calls = []

    def fake_post(url, data=None, json=None, **kwargs):
        calls.append((url, json or data))
        if url == auth.TURNSTILE_URL:  # Cloudflare's test secrets behave like this
            return httpx.Response(200, json={"success": data["secret"] == PASS_SECRET})
        if url.endswith("/otp"):
            return httpx.Response(200, json={})
        if url.endswith("/verify") and json["token"] == "123456":
            return httpx.Response(200, json={"access_token": supabase_jwt(SUB)})
        return httpx.Response(403, json={"msg": "Token has expired or is invalid"})

    monkeypatch.setattr(auth.httpx, "post", fake_post)
    return calls


SUB = uuid.uuid4()


def test_supabase_token_checks(supabase_mode):
    sub = uuid.uuid4()
    assert auth.read_supabase_token(supabase_jwt(sub)) == sub
    other = ec.generate_private_key(ec.SECP256R1())
    bad = [supabase_jwt(key=other),  # forged: not Supabase's key
           supabase_jwt(exp=int(time.time()) - 10),  # expired
           supabase_jwt(aud="anon"),  # not a signed-in user
           supabase_jwt(iss="https://evil.supabase.co/auth/v1"),  # another project
           jwt.encode({"sub": str(sub)}, "a-guessable-hmac-secret-of-32-bytes!", algorithm="HS256"),
           jwt.encode({"sub": str(sub), "aud": "authenticated", "iss": f"{PROJECT}/auth/v1"},
                      KEY, algorithm="ES256"),  # SEC-018: no exp = never expires = refused
           "junk"]
    assert [auth.read_supabase_token(t) for t in bad] == [None] * len(bad)


def test_supabase_mode_accepts_only_supabase_tokens(supabase_mode):
    dev = {"Authorization": f"Bearer {auth.make_token(uuid.uuid4())}"}
    assert client.get("/api/v1/me/reports", headers=dev).status_code == 401
    assert client.post("/api/v1/auth/dev-login", json={"name": "x"}).status_code == 404


def test_otp_routes_are_off_in_dev_mode():
    assert client.post("/api/v1/auth/otp", json={"email": "a@b.co"}).status_code == 404
    assert client.post("/api/v1/auth/verify", json={"email": "a@b.co", "code": "123456",
                                                    "name": "x"}).status_code == 404


def test_turnstile_test_keys(supabase_mode, monkeypatch):
    assert auth.turnstile_ok(TEST_TOKEN)
    monkeypatch.setattr(settings, "turnstile_secret", SecretStr(FAIL_SECRET))
    assert not auth.turnstile_ok(TEST_TOKEN)
    r = client.post("/api/v1/auth/otp", json={"email": "a@b.co", "captcha": TEST_TOKEN})
    assert r.status_code == 400 and r.json()["error"]["message_key"] == "captcha_failed"


@needs_db
def test_otp_login_end_to_end(supabase_mode):
    r = client.post("/api/v1/auth/otp", json={"email": "Priya@Example.com", "captcha": TEST_TOKEN})
    assert r.status_code == 200 and r.json() == {"sent": True}
    bad = client.post("/api/v1/auth/verify",
                      json={"email": "priya@example.com", "code": "000000", "name": "Priya"})
    assert bad.status_code == 400 and bad.json()["error"]["message_key"] == "bad_code"
    good = client.post("/api/v1/auth/verify",
                       json={"email": "priya@example.com", "code": "123456", "name": "Priya"})
    assert good.status_code == 200 and good.json()["user"] == {"id": str(SUB), "name": "Priya"}
    headers = {"Authorization": f"Bearer {good.json()['token']}"}
    assert client.get("/api/v1/me", headers=headers).json()["user"]["name"] == "Priya"


@needs_db
def test_otp_rate_limit_per_email(supabase_mode):
    body = {"email": "spam@example.com", "captcha": TEST_TOKEN}
    codes = [client.post("/api/v1/auth/otp", json=body).status_code
             for _ in range(auth.OTP_LIMIT + 1)]
    assert codes == [200] * auth.OTP_LIMIT + [429]
    other = client.post("/api/v1/auth/otp", json={**body, "email": "me@example.com"})
    assert other.status_code == 200  # someone else can still sign in
    from app.db import connect

    with connect() as conn:  # the blocked 6th try never touched the site-wide counter
        assert conn.execute("SELECT count(*) FROM login_attempt WHERE key = 'all'"
                            ).fetchone()[0] == auth.OTP_LIMIT + 1


def test_prod_refuses_weak_or_missing_settings():  # SEC-007
    s = Settings(_env_file=None, app_env="prod", groq_api_key="", pii_key="change-me")
    bad = prod_problems(s)
    for name in ("GROQ_API_KEY", "PII_KEY", "TURNSTILE_SECRET", "DATABASE_URL", "SUPABASE_URL",
                 "QDRANT_URL", "AUTH_MODE (must be supabase)"):
        assert name in bad
    s = Settings(_env_file=None, app_env="prod", auth_mode="supabase", groq_api_key="g",
                 google_api_key="k", pii_key="p", supabase_anon_key="a",
                 turnstile_secret=PASS_SECRET, qdrant_api_key="q",
                 database_url="postgresql://u:strong@db.example.com:5432/postgres",
                 supabase_url=PROJECT, qdrant_url="https://q.example.com")
    assert prod_problems(s) == ["TURNSTILE_SECRET (Cloudflare test key)"]
    assert prod_problems(Settings(_env_file=None)) == []  # local: anything goes


def test_prod_startup_fails_and_names_no_values(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "app_env", "prod")
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("dev"))
    with pytest.raises(RuntimeError, match="GROQ_API_KEY") as e, TestClient(app):
        pass
    assert "dev@" not in str(e.value)  # names only, never the values


def test_security_headers_and_no_cors_for_strangers():
    r = client.get("/healthz", headers={"Origin": "https://evil.example"})
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert "access-control-allow-origin" not in r.headers

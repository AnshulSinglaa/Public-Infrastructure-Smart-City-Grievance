"""Deploy mini-sprint: Supabase login (email + password), Turnstile, prod settings, headers.

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
PASSWORD = "correct-horse"


def supabase_jwt(sub=None, key=KEY, alg="ES256", **claims) -> str:
    body = {"sub": str(sub or uuid.uuid4()), "aud": "authenticated",
            "iss": f"{PROJECT}/auth/v1", "exp": int(time.time()) + 3600, **claims}
    return jwt.encode(body, key, algorithm=alg)


def signup_body(email="priya@example.com", password=PASSWORD, name="Priya") -> dict:
    return {"email": email, "password": password, "name": name, "captcha": TEST_TOKEN}


def login_body(email="priya@example.com", password=PASSWORD) -> dict:
    return {"email": email, "password": password, "captcha": TEST_TOKEN}


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
        if url.endswith("/signup"):  # "Confirm email" is off: a session comes back at once
            if json["email"] == "taken@example.com":
                return httpx.Response(422, json={"error_code": "user_already_exists",
                                                 "msg": "User already registered"})
            if json["password"] == "password":
                return httpx.Response(422, json={"error_code": "weak_password"})
            if json["email"] == "confirm@example.com":  # "Confirm email" is still on
                return httpx.Response(200, json={"id": str(uuid.uuid4())})
            return httpx.Response(200, json={"access_token": supabase_jwt(SUB)})
        if url.endswith("/token?grant_type=password") and json["password"] == PASSWORD:
            return httpx.Response(200, json={"access_token": supabase_jwt(SUB)})
        return httpx.Response(400, json={"error_code": "invalid_credentials",
                                         "msg": "Invalid login credentials"})

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


def test_supabase_token_tolerates_clock_skew_and_a_trailing_slash(supabase_mode, monkeypatch):
    sub = uuid.uuid4()
    # Supabase's clock a few seconds ahead of ours: its brand-new token must still pass.
    assert auth.read_supabase_token(supabase_jwt(sub, iat=int(time.time()) + 5)) == sub
    # SUPABASE_URL pasted with a trailing slash must not change the issuer we expect.
    monkeypatch.setattr(settings, "supabase_url", PROJECT + "/")
    assert auth.supabase_auth_url() == f"{PROJECT}/auth/v1"
    assert auth.read_supabase_token(supabase_jwt(sub)) == sub


def test_password_routes_are_off_in_dev_mode():
    assert client.post("/api/v1/auth/signup", json=signup_body()).status_code == 404
    assert client.post("/api/v1/auth/login", json=login_body()).status_code == 404


def test_the_email_code_routes_are_gone(supabase_mode):
    assert client.post("/api/v1/auth/otp", json={"email": "a@b.co"}).status_code == 404
    assert client.post("/api/v1/auth/verify", json={"email": "a@b.co", "code": "123456",
                                                    "name": "x"}).status_code == 404


def test_turnstile_test_keys(supabase_mode, monkeypatch):
    assert auth.turnstile_ok(TEST_TOKEN)
    monkeypatch.setattr(settings, "turnstile_secret", SecretStr(FAIL_SECRET))
    assert not auth.turnstile_ok(TEST_TOKEN)
    for path, body in (("signup", signup_body()), ("login", login_body())):
        r = client.post(f"/api/v1/auth/{path}", json=body)
        assert r.status_code == 400 and r.json()["error"]["message_key"] == "captcha_failed"


def test_password_rules_are_checked_before_anything_else(supabase_mode):
    short = client.post("/api/v1/auth/signup", json=signup_body(password="short"))
    assert short.status_code == 422  # min 8: Supabase is never asked
    assert not any(url.endswith("/signup") for url, _ in supabase_mode)


@needs_db
def test_password_sign_up_and_sign_in_end_to_end(supabase_mode):
    up = client.post("/api/v1/auth/signup", json=signup_body(email="Priya@Example.com"))
    assert up.status_code == 200 and up.json()["user"] == {"id": str(SUB), "name": "Priya"}
    taken = client.post("/api/v1/auth/signup", json=signup_body(email="taken@example.com"))
    assert taken.status_code == 409 and taken.json()["error"]["message_key"] == "email_taken"
    weak = client.post("/api/v1/auth/signup", json=signup_body(password="password"))
    assert weak.status_code == 400 and weak.json()["error"]["message_key"] == "weak_password"
    bad = client.post("/api/v1/auth/login", json=login_body(password="wrong-password"))
    assert bad.status_code == 400 and bad.json()["error"]["message_key"] == "bad_credentials"
    good = client.post("/api/v1/auth/login", json=login_body())
    # the name comes back from what was stored at sign-up
    assert good.status_code == 200 and good.json()["user"] == {"id": str(SUB), "name": "Priya"}
    headers = {"Authorization": f"Bearer {good.json()['token']}"}
    assert client.get("/api/v1/me", headers=headers).json()["user"]["name"] == "Priya"


@needs_db
def test_sign_up_without_a_session_means_confirm_email_is_still_on(supabase_mode):
    r = client.post("/api/v1/auth/signup", json=signup_body(email="confirm@example.com"))
    assert r.status_code == 503 and r.json()["error"]["message_key"] == "signup_unavailable"


@needs_db
def test_sign_up_rate_limit_per_email(supabase_mode):
    body = signup_body(email="spam@example.com")
    codes = [client.post("/api/v1/auth/signup", json=body).status_code
             for _ in range(auth.SIGNUP_LIMIT + 1)]
    assert codes == [200] * auth.SIGNUP_LIMIT + [429]
    other = client.post("/api/v1/auth/signup", json=signup_body(email="me@example.com"))
    assert other.status_code == 200  # someone else can still sign up
    from app.db import connect

    with connect() as conn:  # the blocked 6th try never touched the site-wide counter
        assert conn.execute("SELECT count(*) FROM login_attempt WHERE key = 'all'"
                            ).fetchone()[0] == auth.SIGNUP_LIMIT + 1


@needs_db
def test_sign_in_tries_are_limited_per_email_and_leave_others_alone(supabase_mode):
    body = login_body(email="guesser@example.com", password="wrong-password")
    codes = [client.post("/api/v1/auth/login", json=body).status_code
             for _ in range(auth.SIGNIN_LIMIT + 1)]
    assert codes == [400] * auth.SIGNIN_LIMIT + [429]
    assert client.post("/api/v1/auth/login", json=login_body()).status_code in (200, 400)


@needs_db
def test_supabase_outage_is_a_502_not_a_500(supabase_mode, monkeypatch):
    fixture_post = auth.httpx.post  # the fixture's fake: Turnstile still answers

    def fake(url, data=None, json=None, **kwargs):
        if url == auth.TURNSTILE_URL:
            return fixture_post(url, data=data, json=json, **kwargs)
        raise httpx.ConnectTimeout("Supabase is asleep")

    monkeypatch.setattr(auth.httpx, "post", fake)
    for path, body in (("signup", signup_body(email="down@example.com")),
                       ("login", login_body(email="down@example.com"))):
        r = client.post(f"/api/v1/auth/{path}", json=body)
        assert r.status_code == 502 and r.json()["error"]["message_key"] == "login_failed"


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

"""Login. Two modes, picked by AUTH_MODE:

- dev (APP_ENV=local only): POST /auth/dev-login with a name. Token =
  "<user_id>.<expiry unix time>.<HMAC-SHA256 of the first two parts>", signed by us.
- supabase (deploy): POST /auth/otp emails a 6-digit code (after a Turnstile check),
  POST /auth/verify swaps the code for a Supabase access token (a JWT, 1 h). The API checks
  each JWT against Supabase's public keys (JWKS), so it needs no JWT secret at all.

Both return the same {"token", "user"} shape, and every other route just reads
`Authorization: Bearer <token>` through current_user().
"""

import hashlib
import hmac
import secrets
import time
import uuid
from functools import cache
from typing import Annotated

import httpx
import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import local_secret, settings
from app.db import connect
from app.limits import login_limited
from app.privacy.text import decrypt, encrypt

router = APIRouter(prefix="/api/v1")


def check_auth_mode() -> None:
    """Called at startup: dev login on a real server would let anyone be anyone."""
    if settings.auth_mode == "dev" and settings.app_env != "local":
        raise RuntimeError("AUTH_MODE=dev is only allowed with APP_ENV=local")
    if settings.auth_mode not in ("dev", "supabase"):
        raise RuntimeError("AUTH_MODE must be dev or supabase")


# ---------- dev tokens (local only) ----------

@cache
def secret() -> bytes:
    key = settings.dev_token_secret.get_secret_value()
    if not key:
        key = local_secret("dev_token_secret", lambda: secrets.token_hex(32))
    return key.encode()


def sign(payload: str) -> str:
    return hmac.new(secret(), payload.encode(), hashlib.sha256).hexdigest()


def make_token(user_id: uuid.UUID) -> str:
    payload = f"{user_id}.{int(time.time()) + settings.dev_token_days * 86400}"
    return f"{payload}.{sign(payload)}"


def read_token(token: str) -> uuid.UUID | None:
    """The user id, or None if the token is malformed, forged or expired."""
    parts = token.split(".")
    if len(parts) != 3 or not (parts[1].isascii() and parts[1].isdigit()):
        return None
    user_id, expiry, sig = parts
    # compare_digest takes the same time whatever matches, so timing can't leak the signature.
    # Bytes, not str: compare_digest raises TypeError on non-ASCII str (that was a 500).
    good = sign(f"{user_id}.{expiry}").encode()
    if not hmac.compare_digest(sig.encode(), good) or int(expiry) < time.time():
        return None
    return uuid.UUID(user_id)  # signed by us, so it is a valid uuid


# ---------- Supabase tokens (deploy) ----------

@cache
def jwks() -> jwt.PyJWKClient:
    """Supabase's public signing keys, fetched once and cached (new keys are re-fetched)."""
    return jwt.PyJWKClient(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json")


def read_supabase_token(token: str) -> uuid.UUID | None:
    """The user id (`sub`) of a Supabase access token, or None if it is forged, expired, for
    another project or another audience. Only asymmetric algorithms: a token can't pick
    HS256 and get itself checked with a public key (the "alg confusion" attack)."""
    try:
        key = jwks().get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["ES256", "RS256"], audience="authenticated",
                            issuer=f"{settings.supabase_url}/auth/v1",
                            options={"require": ["exp", "sub", "aud", "iss"]})  # SEC-018
        return uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        return None


def current_user(authorization: Annotated[str | None, Header()] = None) -> uuid.UUID:
    """Dependency for every protected route: the caller's user id, or 401."""
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        user_id = read_token(token) if settings.auth_mode == "dev" else read_supabase_token(token)
        if user_id:
            return user_id
    raise HTTPException(401)  # main.py turns this into {"code": "unauthorized", ...}


# ---------- login helpers ----------

LOGIN_LIMIT = 10  # dev-login: 10 per IP per 10 minutes
OTP_LIMIT, VERIFY_LIMIT, ALL_LIMIT = 5, 10, 100  # codes sent / tried per email; whole site
RATE_LIMITED = {"error": {"code": "rate_limited", "message_key": "rate_limited_login"}}
TURNSTILE_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def limited(*checks: tuple[str, int]) -> bool:
    """Is any (key, limit) over its limit? Its own connection, so the try is saved
    (committed) even when the login itself then fails."""
    with connect() as conn:
        return any(login_limited(conn, key, limit) for key, limit in checks)


def email_key(email: str) -> str:
    """Rate-limit key for an email: a hash, so the table never holds the address."""
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:32]


def turnstile_ok(token: str) -> bool:
    """Cloudflare checks the widget's one-time token with our secret. No secret = local."""
    secret_key = settings.turnstile_secret.get_secret_value()
    if not secret_key:
        return settings.app_env == "local"
    try:
        r = httpx.post(TURNSTILE_URL, data={"secret": secret_key, "response": token},
                       timeout=10)
        return r.json().get("success") is True
    except (httpx.HTTPError, ValueError, AttributeError):
        return False  # can't check = no entry


def supabase(path: str, body: dict) -> httpx.Response:
    """One call to Supabase Auth's REST API. The `apikey` header takes either the legacy anon
    key or the newer publishable key (sb_publishable_...)."""
    return httpx.post(f"{settings.supabase_url}/auth/v1/{path}", json=body, timeout=15,
                      headers={"apikey": settings.supabase_anon_key.get_secret_value()})


def error(status: int, code: str, message_key: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message_key": message_key}},
                        status_code=status)


def save_user(user_id: uuid.UUID, name: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO app_user (id, name) VALUES (%s, %s)"
            " ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name",
            (user_id, encrypt(name)),
        )


# ---------- endpoints ----------

EMAIL = r"^[^@\s]+@[^@\s]+$"


class DevLogin(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str | None = Field(None, max_length=254, pattern=EMAIL)


class OtpRequest(BaseModel):
    email: str = Field(max_length=254, pattern=EMAIL)
    captcha: str = Field("", max_length=2048)  # the Turnstile widget's one-time token


class OtpVerify(BaseModel):
    email: str = Field(max_length=254, pattern=EMAIL)
    code: str = Field(pattern=r"^\d{6,10}$")
    name: str = Field(min_length=1, max_length=80)


@router.post("/auth/dev-login")
def dev_login(body: DevLogin, request: Request):
    if settings.auth_mode != "dev":
        raise HTTPException(404)
    if limited((f"ip:{request.client.host if request.client else 'unknown'}", LOGIN_LIMIT)):
        return JSONResponse(RATE_LIMITED, status_code=429)
    name = body.name.strip()
    # Same email (or name) -> same id, so logging in again finds your old reports.
    # The email is only hashed into the id, never stored (ARCH §0: email lives in Supabase).
    user_id = uuid.uuid5(uuid.NAMESPACE_URL, "dev-login:" + (body.email or name).lower())
    save_user(user_id, name)
    return {"token": make_token(user_id), "user": {"id": str(user_id), "name": name}}


@router.post("/auth/otp")
def send_code(body: OtpRequest):
    """Step 1: Turnstile, rate limits, then Supabase emails a code. The UI calls us from its
    server, so all users share one IP: the limits are per email plus one site-wide cap."""
    if settings.auth_mode != "supabase":
        raise HTTPException(404)
    if not turnstile_ok(body.captcha):
        return error(400, "captcha_failed", "captcha_failed")
    # Per email first: an email over its limit stops here and never uses up the site-wide
    # cap, so one abuser can't lock everyone out. (No per-IP limit: every caller is the
    # Streamlit server, so all users share one IP.) limited() stops at the first full key.
    if limited((f"otp:{email_key(body.email)}", OTP_LIMIT), ("all", ALL_LIMIT)):
        return JSONResponse(RATE_LIMITED, status_code=429)
    r = supabase("otp", {"email": body.email, "create_user": True})
    if r.status_code == 429:  # Supabase's own email limit
        return JSONResponse(RATE_LIMITED, status_code=429)
    if r.is_error:
        return error(502, "login_failed", "login_failed")
    return {"sent": True}


@router.post("/auth/verify")
def verify_code(body: OtpVerify):
    """Step 2: the emailed code -> a Supabase access token, checked like any other token."""
    if settings.auth_mode != "supabase":
        raise HTTPException(404)
    if limited((f"verify:{email_key(body.email)}", VERIFY_LIMIT)):
        return JSONResponse(RATE_LIMITED, status_code=429)
    r = supabase("verify", {"type": "email", "email": body.email, "token": body.code})
    token = r.json().get("access_token") if r.is_success else None
    user_id = read_supabase_token(token) if isinstance(token, str) else None
    if not user_id:
        return error(400, "bad_code", "bad_code")
    name = body.name.strip()
    save_user(user_id, name)
    return {"token": token, "user": {"id": str(user_id), "name": name}}


@router.get("/me")
def me(user_id: Annotated[uuid.UUID, Depends(current_user)]):
    """Who owns this token? The UI calls it once to check a token it kept in the browser
    across a refresh. It creates nothing, so it is not under the login rate limit."""
    with connect() as conn:
        row = conn.execute("SELECT name FROM app_user WHERE id = %s", (user_id,)).fetchone()
    if not row or row[0] is None:  # signed, but the user is gone (e.g. DB reset)
        raise HTTPException(401)
    return {"user": {"id": str(user_id), "name": decrypt(row[0])}}

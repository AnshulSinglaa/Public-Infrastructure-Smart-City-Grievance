"""Login. Two modes, picked by AUTH_MODE:

- dev (APP_ENV=local only): POST /auth/dev-login with a name. Token =
  "<user_id>.<expiry unix time>.<HMAC-SHA256 of the first two parts>", signed by us.
- supabase (deploy): email + password. POST /auth/signup creates the account and POST
  /auth/login signs in (both after a Turnstile check); Supabase answers with an access token
  (a JWT, 1 h). No email is sent, so no SMTP is needed: the Supabase project must have
  "Confirm email" switched OFF (Authentication -> Providers -> Email). The API checks each JWT
  against Supabase's public keys (JWKS), so it needs no JWT secret at all.

Both return the same {"token", "user"} shape, and every other route just reads
`Authorization: Bearer <token>` through current_user().
"""

import hashlib
import hmac
import logging
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

log = logging.getLogger(__name__)
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

def supabase_auth_url() -> str:
    """The project's Auth base URL. A trailing slash pasted into SUPABASE_URL must not change
    the issuer we check (every login would then fail) or double the slash in the JWKS and
    REST URLs."""
    return f"{settings.supabase_url.strip().rstrip('/')}/auth/v1"


@cache
def jwks() -> jwt.PyJWKClient:
    """Supabase's public signing keys, fetched once and cached (new keys are re-fetched)."""
    return jwt.PyJWKClient(f"{supabase_auth_url()}/.well-known/jwks.json")


def read_supabase_token(token: str) -> uuid.UUID | None:
    """The user id (`sub`) of a Supabase access token, or None if it is forged, expired, for
    another project or another audience. Only asymmetric algorithms: a token can't pick
    HS256 and get itself checked with a public key (the "alg confusion" attack)."""
    try:
        key = jwks().get_signing_key_from_jwt(token).key
        # SEC-018: exp is required and stays strict. `iat` is not checked: Supabase stamps it
        # with its own clock, and when ours is a second behind PyJWT calls a brand-new token
        # "not yet valid", so a correct login failed.
        claims = jwt.decode(token, key, algorithms=["ES256", "RS256"], audience="authenticated",
                            issuer=supabase_auth_url(),
                            options={"require": ["exp", "sub", "aud", "iss"],
                                     "verify_iat": False})
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
# Per email: sign-ups and sign-in tries; and the site-wide cap on new sign-ups. (No per-IP
# limit: every caller is the Streamlit server, so all users share one IP.)
SIGNUP_LIMIT, SIGNIN_LIMIT, ALL_LIMIT = 5, 10, 100
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
    return httpx.post(f"{supabase_auth_url()}/{path}", json=body, timeout=15,
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


def stored_name(user_id: uuid.UUID) -> str | None:
    """The name this user signed up with, or None if there is no row (e.g. a DB reset)."""
    with connect() as conn:
        row = conn.execute("SELECT name FROM app_user WHERE id = %s", (user_id,)).fetchone()
    return decrypt(row[0]) if row and row[0] is not None else None


def body_of(r: httpx.Response) -> dict:
    """Supabase's JSON answer as a dict ({} if it is anything else)."""
    data = r.json()
    return data if isinstance(data, dict) else {}


def token_user(data: dict) -> tuple[str | None, uuid.UUID | None]:
    """(access token, its user id) from a Supabase answer; user id is None unless the token
    passes our own checks."""
    token = data.get("access_token")
    if not isinstance(token, str):
        return None, None
    user_id = read_supabase_token(token)
    if not user_id:  # Supabase signed someone in but we refuse its token: a setup problem
        log.warning("Supabase issued a token that failed our checks (SUPABASE_URL, signing "
                    "keys or server clock?)")
    return token, user_id


# ---------- endpoints ----------

EMAIL = r"^[^@\s]+@[^@\s]+$"


class DevLogin(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str | None = Field(None, max_length=254, pattern=EMAIL)


class SignUp(BaseModel):
    email: str = Field(max_length=254, pattern=EMAIL)
    password: str = Field(min_length=8, max_length=72)  # 72: bcrypt's limit
    name: str = Field(min_length=1, max_length=80)
    captcha: str = Field("", max_length=2048)  # the Turnstile widget's one-time token


class SignIn(BaseModel):
    email: str = Field(max_length=254, pattern=EMAIL)
    password: str = Field(min_length=1, max_length=72)
    captcha: str = Field("", max_length=2048)


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


@router.post("/auth/signup")
def sign_up(body: SignUp):
    """Create an account: Turnstile, rate limits, then Supabase makes the user and (with
    "Confirm email" off) hands back a session straight away."""
    if settings.auth_mode != "supabase":
        raise HTTPException(404)
    if not turnstile_ok(body.captcha):
        return error(400, "captcha_failed", "captcha_failed")
    # Per email first: an email over its limit stops here and never uses up the site-wide
    # cap, so one abuser can't lock everyone out. limited() stops at the first full key.
    if limited((f"signup:{email_key(body.email)}", SIGNUP_LIMIT), ("all", ALL_LIMIT)):
        return JSONResponse(RATE_LIMITED, status_code=429)
    try:
        r = supabase("signup", {"email": body.email, "password": body.password})
        data = body_of(r)
    except (httpx.HTTPError, ValueError):  # Supabase down, slow, or not JSON
        return error(502, "login_failed", "login_failed")
    if r.status_code == 429:  # Supabase's own rate limit
        return JSONResponse(RATE_LIMITED, status_code=429)
    if r.is_error:
        why = data.get("error_code")
        if why == "user_already_exists":
            return error(409, "email_taken", "email_taken")
        if why == "weak_password":
            return error(400, "weak_password", "weak_password")
        if why == "email_address_invalid":
            return error(400, "invalid_email", "bad_email")
        return error(502, "login_failed", "login_failed")
    token, user_id = token_user(data)
    if token is None:  # a user but no session: "Confirm email" is still on in Supabase
        log.warning("Supabase sign-up returned no session: switch OFF 'Confirm email' "
                    "(Authentication -> Providers -> Email)")
        return error(503, "signup_unavailable", "signup_unavailable")
    if not user_id:
        return error(502, "login_failed", "login_failed")
    name = body.name.strip()
    save_user(user_id, name)
    return {"token": token, "user": {"id": str(user_id), "name": name}}


@router.post("/auth/login")
def sign_in(body: SignIn):
    """Sign in with email + password: Turnstile, a per-email try limit, then Supabase checks
    the password. A wrong password and an unknown email give the same answer."""
    if settings.auth_mode != "supabase":
        raise HTTPException(404)
    if not turnstile_ok(body.captcha):
        return error(400, "captcha_failed", "captcha_failed")
    if limited((f"signin:{email_key(body.email)}", SIGNIN_LIMIT)):
        return JSONResponse(RATE_LIMITED, status_code=429)
    try:
        r = supabase("token?grant_type=password",
                     {"email": body.email, "password": body.password})
        data = body_of(r)
    except (httpx.HTTPError, ValueError):
        return error(502, "login_failed", "login_failed")
    if r.status_code == 429:
        return JSONResponse(RATE_LIMITED, status_code=429)
    if r.is_error:
        if r.status_code >= 500:
            return error(502, "login_failed", "login_failed")
        return error(400, "bad_credentials", "bad_credentials")
    _, user_id = token_user(data)
    if not user_id:
        return error(502, "login_failed", "login_failed")
    name = stored_name(user_id) or body.email.split("@")[0]
    save_user(user_id, name)  # makes sure the row exists: reports and /me need it
    return {"token": data["access_token"], "user": {"id": str(user_id), "name": name}}


@router.get("/me")
def me(user_id: Annotated[uuid.UUID, Depends(current_user)]):
    """Who owns this token? The UI calls it once to check a token it kept in the browser
    across a refresh. It creates nothing, so it is not under the login rate limit."""
    with connect() as conn:
        row = conn.execute("SELECT name FROM app_user WHERE id = %s", (user_id,)).fetchone()
    if not row or row[0] is None:  # signed, but the user is gone (e.g. DB reset)
        raise HTTPException(401)
    return {"user": {"id": str(user_id), "name": decrypt(row[0])}}

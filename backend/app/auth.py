"""Dev login (Sprint 3). Supabase email OTP replaces it in Sprint 8 with the same endpoints.

Token = "<user_id>.<expiry unix time>.<HMAC-SHA256 of the first two parts>".
Only the server knows the secret, so a client can't forge or edit a token.
"""

import hashlib
import hmac
import secrets
import time
import uuid
from collections import defaultdict, deque
from functools import cache
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import local_secret, settings
from app.db import connect
from app.privacy.text import decrypt, encrypt

router = APIRouter(prefix="/api/v1")


def check_auth_mode() -> None:
    """Called at startup: dev login on a real server would let anyone be anyone."""
    if settings.auth_mode == "dev" and settings.app_env != "local":
        raise RuntimeError("AUTH_MODE=dev is only allowed with APP_ENV=local")


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


def current_user(authorization: Annotated[str | None, Header()] = None) -> uuid.UUID:
    """Dependency for every protected route: the caller's user id, or 401."""
    if settings.auth_mode == "dev" and authorization and authorization.startswith("Bearer "):
        user_id = read_token(authorization.removeprefix("Bearer ").strip())
        if user_id:
            return user_id
    raise HTTPException(401)  # main.py turns this into {"code": "unauthorized", ...}


LOGIN_LIMIT, LOGIN_WINDOW_S = 10, 600  # 10 logins per IP per 10 minutes
# ponytail: per-process memory, so it resets on restart and each worker counts alone;
# moves to Redis or Supabase's own auth rate limits at deploy.
login_times: dict[str, deque] = defaultdict(deque)


def login_limited(ip: str) -> bool:
    """Sliding window: drop timestamps older than the window, then count what is left."""
    now, times = time.monotonic(), login_times[ip]
    while times and times[0] < now - LOGIN_WINDOW_S:
        times.popleft()
    if len(times) >= LOGIN_LIMIT:
        return True
    times.append(now)
    return False


class DevLogin(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str | None = Field(None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+$")


@router.post("/auth/dev-login")
def dev_login(body: DevLogin, request: Request):
    if settings.auth_mode != "dev":
        raise HTTPException(404)
    if login_limited(request.client.host if request.client else "unknown"):
        return JSONResponse({"error": {"code": "rate_limited", "message_key": "rate_limited_login"}},
                            status_code=429)
    name = body.name.strip()
    # Same email (or name) -> same id, so logging in again finds your old reports.
    # The email is only hashed into the id, never stored (ARCH §0: email lives in Supabase).
    user_id = uuid.uuid5(uuid.NAMESPACE_URL, "dev-login:" + (body.email or name).lower())
    with connect() as conn:
        conn.execute(
            "INSERT INTO app_user (id, name) VALUES (%s, %s)"
            " ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name",
            (user_id, encrypt(name)),
        )
    return {"token": make_token(user_id), "user": {"id": str(user_id), "name": name}}


@router.get("/me")
def me(user_id: Annotated[uuid.UUID, Depends(current_user)]):
    """Who owns this token? The UI calls it once to check a token it kept in the browser
    across a refresh. It creates nothing, so it is not under the dev-login rate limit."""
    with connect() as conn:
        row = conn.execute("SELECT name FROM app_user WHERE id = %s", (user_id,)).fetchone()
    if not row or row[0] is None:  # signed, but the user is gone (e.g. DB reset)
        raise HTTPException(401)
    return {"user": {"id": str(user_id), "name": decrypt(row[0])}}

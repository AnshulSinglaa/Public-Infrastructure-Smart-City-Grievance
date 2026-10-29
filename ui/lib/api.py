"""The only place the UI talks to the backend.

Contracts: docs/contracts/sprint2-api.md and sprint3-api.md. Every call except health and
login takes the user's token and sends it as `Authorization: Bearer <token>`.
"""

import os

import httpx

from strings import TEXT

# 127.0.0.1, not localhost: uvicorn listens on IPv4 only, and Windows tries IPv6 (::1) first,
# which costs ~2 s per call before it falls back.
API_URL = os.getenv("API_URL", "http://127.0.0.1:8010")


class ApiError(Exception):
    """Carries a message_key so the page can show friendly text from strings.py."""

    def __init__(self, message_key: str):
        super().__init__(message_key)
        self.message_key = message_key


def _client(token: str | None = None) -> httpx.Client:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if os.getenv("FAKE_API") == "1":
        from lib.fake_api import transport  # canned answers, no server needed

        return httpx.Client(base_url=API_URL, transport=transport, headers=headers, timeout=30)
    # 30 s: AI steps can take a few seconds
    return httpx.Client(base_url=API_URL, headers=headers, timeout=30)


def _check(resp: httpx.Response) -> dict:
    if resp.is_error:
        try:
            key = resp.json()["error"]["message_key"]
        except (ValueError, KeyError, TypeError):  # not our error shape (e.g. a plain 500)
            key = "server_error"
        # A key the UI has no text for gets the generic message, never the raw key.
        raise ApiError(key if key in TEXT else "server_error")
    return resp.json()


def _call(method: str, path: str, token: str | None = None, **kwargs) -> dict:
    """One request + error handling, shared by every endpoint below."""
    try:
        with _client(token) as c:
            return _check(c.request(method, f"/api/v1{path}", **kwargs))
    except httpx.HTTPError:  # server down, timeout, DNS
        raise ApiError("api_unreachable") from None


def health() -> bool:
    try:
        with _client() as c:
            return c.get("/healthz", params={"db": 1}, timeout=3).json()["ok"]
    except (httpx.HTTPError, ValueError, KeyError):  # no server, bad JSON, wrong shape
        return False


def dev_login(name: str, email: str) -> dict:
    """Returns {"token": ..., "user": {"id", "name"}}."""
    body = {"name": name, "email": email} if email else {"name": name}
    return _call("POST", "/auth/dev-login", json=body)


def me(token: str) -> dict:
    """{"id", "name"} of the token's user. Checks a token kept across a refresh."""
    return _call("GET", "/me", token)["user"]


def create_report(token: str, text: str, photo: tuple[str, bytes, str] | None, pin: dict,
                  verified: bool) -> dict:
    """POST /reports as multipart. `photo` is (filename, bytes, mime type) or None.

    `pin` is {"lat", "lng", "source", "acc"} from the pin-confirm card (acc may be None).
    """
    # Every field goes in `files` so httpx always sends multipart/form-data. With `data=` and
    # no file, httpx would quietly switch to url-encoded. (None, value) = a plain text field.
    parts = {
        "ui_lang": (None, "en"),  # the UI is English only (UX §14)
        "lat": (None, str(pin["lat"])),
        "lng": (None, str(pin["lng"])),
        "pin_source": (None, pin["source"]),
        "location_verified": (None, "true" if verified else "false"),
    }
    if pin.get("acc") is not None:
        parts["accuracy_m"] = (None, str(round(pin["acc"], 1)))
    if text:
        parts["text"] = (None, text)
    if photo:
        parts["photo"] = photo
    return _call("POST", "/reports", token, files=parts)


def get_dossier(token: str, dossier_id: str) -> dict:
    return _call("GET", f"/dossiers/{dossier_id}", token)


def get_photo(token: str, photo_url: str) -> bytes | None:
    """The dossier's (blurred) photo, or None if it is missing. Used when reopening a report."""
    try:
        with _client(token) as c:
            resp = c.get(photo_url)
            return resp.content if resp.is_success else None
    except httpx.HTTPError:
        return None


def geocode(token: str, text: str) -> list[dict]:
    """Up to 5 places for a landmark: [{"lat", "lng", "label", "source"}]."""
    return _call("POST", "/geocode", token, json={"text": text})["candidates"]


def my_reports(token: str) -> list[dict]:
    return _call("GET", "/me/reports", token)["reports"]

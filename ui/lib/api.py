"""The only place the UI talks to the backend (contract: docs/contracts/sprint2-api.md)."""

import os

import httpx

from i18n import TEXT

# 127.0.0.1, not localhost: uvicorn listens on IPv4 only, and Windows tries IPv6 (::1) first,
# which costs ~2 s per call before it falls back.
API_URL = os.getenv("API_URL", "http://127.0.0.1:8010")


class ApiError(Exception):
    """Carries an i18n key so the page can show a friendly message in either language."""

    def __init__(self, message_key: str):
        super().__init__(message_key)
        self.message_key = message_key


def _client() -> httpx.Client:
    if os.getenv("FAKE_API") == "1":
        from lib.fake_api import transport  # canned answers, no server needed

        return httpx.Client(base_url=API_URL, transport=transport, timeout=30)
    return httpx.Client(base_url=API_URL, timeout=30)  # AI steps can take a few seconds


def _check(resp: httpx.Response) -> dict:
    if resp.is_error:
        try:
            key = resp.json()["error"]["message_key"]
        except (ValueError, KeyError, TypeError):  # not our error shape (e.g. a plain 500)
            key = "server_error"
        # A key the UI has no text for gets the generic message, never the raw key.
        raise ApiError(key if key in TEXT["en"] else "server_error")
    return resp.json()


def health() -> bool:
    try:
        with _client() as c:
            return c.get("/healthz", params={"db": 1}, timeout=3).json()["ok"]
    except (httpx.HTTPError, ValueError, KeyError):  # no server, bad JSON, wrong shape
        return False


def create_report(text: str, photo: tuple[str, bytes, str] | None, lang: str) -> dict:
    """POST /reports as multipart. `photo` is (filename, bytes, mime type) or None."""
    # Every field goes in `files` so httpx always sends multipart/form-data. With `data=` and
    # no file, httpx would quietly switch to url-encoded. (None, value) = a plain text field.
    parts = {"ui_lang": (None, lang)}
    if text:
        parts["text"] = (None, text)
    if photo:
        parts["photo"] = photo
    try:
        with _client() as c:
            return _check(c.post("/api/v1/reports", files=parts))
    except httpx.HTTPError:  # server down, timeout, DNS
        raise ApiError("api_unreachable") from None


def get_dossier(dossier_id: str) -> dict:
    try:
        with _client() as c:
            return _check(c.get(f"/api/v1/dossiers/{dossier_id}"))
    except httpx.HTTPError:
        raise ApiError("api_unreachable") from None

import uuid

import pytest
from api_helpers import client

from app.auth import login_times, make_token


@pytest.fixture
def headers():
    """A fresh dev user (real login, needs the DB), so rate limits never leak across tests."""
    r = client.post("/api/v1/auth/dev-login", json={"name": f"test-{uuid.uuid4().hex[:8]}"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def token_headers():
    """A valid token for a user that is NOT in the DB: enough for validation-only tests."""
    return {"Authorization": f"Bearer {make_token(uuid.uuid4())}"}


@pytest.fixture(autouse=True)
def fresh_login_limit():
    """Every test client shares the IP "testclient", so reset the login limit per test."""
    login_times.clear()


@pytest.fixture(autouse=True)
def fake_models(monkeypatch, tmp_path_factory):
    """Tests never call real models: scripted fakes, a fresh LLM cache, no retry waits."""
    from app.config import settings
    from app.providers import common, fakes

    monkeypatch.setattr(settings, "provider_mode", "fake")
    monkeypatch.setattr(settings, "llm_cache_dir", tmp_path_factory.mktemp("llm"))
    monkeypatch.setattr(common, "BACKOFF_S", 0)
    fakes.script()

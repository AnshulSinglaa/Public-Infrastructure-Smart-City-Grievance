import uuid
from urllib.parse import urlparse

import pytest

from app.config import settings

# Tests create users and reports, so they must never run against a cloud database (a .env
# filled with deploy values once pointed them at Supabase). Local Docker DB only.
_db_host = urlparse(settings.database_url).hostname
if _db_host not in ("127.0.0.1", "localhost", "db", None):
    pytest.exit(f"Refusing to run tests: DATABASE_URL points at {_db_host}, not the local DB. "
                "Put the local value back in .env (see .env.example).", returncode=2)

from api_helpers import client  # noqa: E402  (imports the app, after the safety check)

from app.auth import make_token  # noqa: E402


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
    from test_health import db_is_up

    from app.db import connect

    if db_is_up():
        with connect() as conn:
            conn.execute("DELETE FROM login_attempt")


@pytest.fixture(autouse=True)
def fake_models(monkeypatch, tmp_path_factory):
    """Tests never call real models: scripted fakes, a fresh LLM cache, no retry waits."""
    from app.config import settings
    from app.providers import common, fakes

    monkeypatch.setattr(settings, "provider_mode", "fake")
    monkeypatch.setattr(settings, "llm_cache_dir", tmp_path_factory.mktemp("llm"))
    monkeypatch.setattr(common, "BACKOFF_S", 0)
    fakes.script()


@pytest.fixture(scope="session", autouse=True)
def routing_data():
    """Rules + departments in the DB, and a plain inner lane at the usual test pin
    (COVERED), so a pothole there routes to MCD without asking a question."""
    from api_helpers import COVERED, add_test_road, remove_test_roads, seed_routing_data
    from test_health import db_is_up

    from app.db import connect

    if not db_is_up():
        yield
        return
    with connect() as conn:
        seed_routing_data(conn)
        remove_test_roads(conn)
        add_test_road(conn, float(COVERED["lat"]), float(COVERED["lng"]), name="Test lane",
                      highway_class="residential")
    yield
    with connect() as conn:
        remove_test_roads(conn)

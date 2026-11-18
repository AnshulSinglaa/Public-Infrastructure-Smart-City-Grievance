import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

import pytest

from app.config import settings

# Tests create users and reports, so they must never run against a cloud database (a .env
# filled with deploy values once pointed them at Supabase). Local Docker DB only.
_db = urlparse(settings.database_url)
if _db.hostname not in ("127.0.0.1", "localhost", "db", None):
    pytest.exit(f"Refusing to run tests: DATABASE_URL points at {_db.hostname}, not the local "
                "DB. Put the local value back in .env (see .env.example).", returncode=2)

# ...and never against the dev/QA database either (REVIEW R-07): always <name>_test on the
# same local server (civic -> civic_test). The first run creates and loads it with
# scripts/load_all.py (~2 min); later runs only apply new migrations. Must happen here, before
# any test module is imported (their skipif checks query the DB at import time).
_TEST_DB = _db.path.lstrip("/").removesuffix("_test") + "_test"
settings.database_url = _db._replace(path="/" + _TEST_DB).geturl()
os.environ["DATABASE_URL"] = settings.database_url  # for scripts/alembic run below


def _prepare_test_db() -> None:
    import psycopg

    root = Path(__file__).resolve().parents[2]
    server = _db._replace(path="/postgres").geturl()
    try:
        with psycopg.connect(server, connect_timeout=1) as conn:
            exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s",
                                  (_TEST_DB,)).fetchone()
    except psycopg.Error:
        return  # no local Postgres: the DB tests skip themselves
    cmd = ["scripts/load_all.py"] if not exists else ["-m", "alembic", "upgrade", "head"]
    subprocess.run([sys.executable, *cmd], cwd=root, check=True, stdout=subprocess.DEVNULL)


_prepare_test_db()

from api_helpers import client

from app.auth import make_token


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
    from app.llm import gateway
    from app.providers import common, fakes

    monkeypatch.setattr(settings, "provider_mode", "fake")
    gateway.failures.clear()  # circuit breaker closed, throttle empty at the start of a test
    gateway.spent.clear()
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

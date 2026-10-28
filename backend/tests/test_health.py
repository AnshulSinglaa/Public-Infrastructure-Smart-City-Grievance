import psycopg
import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.main import app, check_qdrant

client = TestClient(app)


def db_is_up() -> bool:
    try:
        with connect(timeout=1):
            return True
    except psycopg.Error:
        return False


def test_healthz_without_db():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.skipif(not db_is_up(), reason="database not reachable")
@pytest.mark.skipif(not check_qdrant(), reason="qdrant not reachable")
def test_healthz_with_db():
    body = client.get("/healthz?db=1").json()
    assert body["ok"] is True
    assert body["db"] == {"select1": True, "postgis": True, "qdrant": True}

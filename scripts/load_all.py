"""One command for a fresh database (local Docker or Supabase): tables, then all the data.

Run from the repo root:  uv run python scripts/load_all.py
It uses DATABASE_URL: the shell's value if set, else .env (same rule as the API).
For Supabase, set it in this PowerShell window only (it is not saved anywhere):
    $env:DATABASE_URL = "<Supabase session pooler URI>"; uv run python scripts/load_all.py
Every step is safe to re-run (migrations skip what is done, loaders upsert).
On a local server it first creates the database if it is missing (e.g. civic_test for pytest).
"""
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.config import settings  # after the path line above

PY = sys.executable
STEPS = [
    [PY, "-m", "alembic", "upgrade", "head"],  # tables, indexes, RLS (backend/migrations)
    [PY, "scripts/load_wards.py"],   # 250 wards + special zones (NDMC, discoms, ...)
    [PY, "scripts/load_rules.py"],   # departments, contacts, 73 rules, OSM roads
    [PY, "scripts/load_states.py"],  # 36 state/UT outlines for outside-Delhi pins
]


LOCAL_HOSTS = ("127.0.0.1", "localhost", "db")


def create_if_missing(url: str) -> bool:
    """Local server only (never Supabase): CREATE DATABASE <name> if it doesn't exist.
    True if it was created."""
    u = urlparse(url)
    name = u.path.lstrip("/")
    if u.hostname not in LOCAL_HOSTS or not name.isidentifier():
        return False
    with psycopg.connect(u._replace(path="/postgres").geturl(), autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            return False
        conn.execute(f'CREATE DATABASE "{name}"')  # name checked by isidentifier() above
    print("created database", name)
    return True


def main():
    create_if_missing(settings.database_url)
    env = {**os.environ, "DATABASE_URL": settings.database_url}  # the scripts read this
    for step in STEPS:
        print("==>", *step[1:], flush=True)
        subprocess.run(step, cwd=ROOT, env=env, check=True)
    print("Done: database ready.")


if __name__ == "__main__":
    main()

"""One command for a fresh database (local Docker or Supabase): tables, then all the data.

Run from the repo root:  uv run python scripts/load_all.py
It uses DATABASE_URL: the shell's value if set, else .env (same rule as the API).
For Supabase, set it in this PowerShell window only (it is not saved anywhere):
    $env:DATABASE_URL = "<Supabase session pooler URI>"; uv run python scripts/load_all.py
Every step is safe to re-run (migrations skip what is done, loaders upsert).
"""
import os
import subprocess
import sys
from pathlib import Path

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


def main():
    env = {**os.environ, "DATABASE_URL": settings.database_url}  # the scripts read this
    for step in STEPS:
        print("==>", *step[1:], flush=True)
        subprocess.run(step, cwd=ROOT, env=env, check=True)
    print("Done: database ready.")


if __name__ == "__main__":
    main()

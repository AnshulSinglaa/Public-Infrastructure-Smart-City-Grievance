# Civic Grievance Router

> **Snap a photo, write a line in Hindi or English, and find out which government department owns that exact spot, the official rule that proves it, and a complaint ready to file.**

A **multimodal agentic RAG** application for civic complaints, with geospatial routing. **Pilot city: Delhi.** The code is city-agnostic: wards, roads, rules and contacts are data files.

![status](https://img.shields.io/badge/status-Sprint%202%20done-blue) ![python](https://img.shields.io/badge/python-3.12-blue) ![tests](https://img.shields.io/badge/tests-29%20passing-brightgreen)

---

## The problem

In Delhi, a pothole on a road **≥ 60 ft wide** belongs to **PWD**, but on a narrower lane it's **MCD**'s. Sewers belong to **DJB**, big trunk drains to **Irrigation & Flood Control**, and NDMC and the Cantonment run their own areas. Citizens don't know where to complain, and departments bounce complaints back ("not my department"). Existing portals (CPGRAMS, MCD 311, Swachhata) all assume you already know the right department.

## What Civic Grievance Router does

```
 photo + text (हिंदी / English / Hinglish) + GPS
        │
        ▼
 understand the problem ──► find the owner of THAT spot ──► cite the rule ──► ready-to-file complaint
   (multimodal AI)            (geospatial SQL, PostGIS)       (RAG)            (+ call script, tracker)
```

The app **never files complaints on the citizen's behalf**: it routes and prepares, and the citizen files on the official channel. It **never guesses**: when it's unsure it asks, and when it doesn't cover an area it says so and shows the general helplines.

## Current status (Sprint 2 of 14)

| Done ✅ | Next 🔜 |
|---|---|
| Report screen: photo (camera or gallery) + text, Hindi/English UI | Sprint 3: real GPS pin + map, login, photo privacy (EXIF strip, blur), 25 wards |
| **Real ward lookup** (PostGIS point-in-polygon) on 8 coverage wards | Sprint 4: **real AI** (Gemini vision + Groq LLM in a LangGraph agent), emergency path, Langfuse |
| "Not covered yet" path with helplines (never a fake department) | Sprint 5: road-level routing (OSM roads + PWD list + 73 rules) |
| Bilingual complaint dossier + 📞 call button | Sprint 6: RAG over rule documents (Qdrant), cited claims, Ragas |
| Postgres + PostGIS + **Qdrant** running in Docker | Sprint 7–8: duplicates, Call mode, privacy, full evaluation, Locust, deploy |
| Data: 32 departments, 37 problem types, 73 routing rules, ward maps, 4,918 roads, PWD road list | Later: tracker + appeals, votes, **conversational chat** |
| 29 automated tests, CI, 11 bugs found and fixed | |

> ⚠️ In Sprints 1–2 the **AI is simulated** (a fixed answer, shown in the UI as "Demo: AI is simulated"). The full pipeline runs end to end, and real models replace the fakes in Sprint 4.

## Architecture

```
 ┌──────────────────────────────────────────────┐
 │  Streamlit UI  (:8501)  हिंदी / English        │
 └──────────────────────────────────────────────┘
          │ POST /api/v1/reports    ▲ dossier / next step
          ▼                         │
 ┌──────────────────────────────────────────────┐
 │  FastAPI backend  (:8010)                    │
 │  validate · privacy checks · call modules    │
 └──────────────────────────────────────────────┘
          │
          ▼
 ┌──────────────────────────────────────────────┐   photo   ┌──────────────┐
 │  Intake agent (LangGraph, from Sprint 4)     │──────────►│ Gemini Flash │
 │  understand · ask when unsure · draft letter │   text    ├──────────────┤
 └──────────────────────────────────────────────┘──────────►│ Groq LLM     │
          │ plain Python (no AI decides the department)     └──────────────┘
          ▼
 ┌──────────────────────────────────────────────┐
 │  Engines: location · routing (SQL) · dedup · │
 │  RAG citations · dossier · tracker · privacy │
 └──────────────────────────────────────────────┘
          │ read / write
          ▼
 ┌─────────────────────────────┐   ┌──────────────────────────────┐
 │ PostgreSQL 16 + PostGIS     │   │ Qdrant (vector DB)           │
 │ reports · wards · roads ·   │◄─►│ rule-chunk vectors (RAG)     │
 │ rules · rule TEXT to quote  │id │ photo vectors (duplicates)   │
 └─────────────────────────────┘   └──────────────────────────────┘
```

Design rules: **the LLM never picks the department** (SQL does), **numbers (days, helplines) come from the database, never from the AI**, and **every generated claim must quote a real source**.

## Tech stack

| Layer | Tools |
|---|---|
| UI | Streamlit (custom dark theme), streamlit-folium (map, Sprint 3) |
| API | FastAPI, Pydantic, Uvicorn |
| Agent | LangGraph (from Sprint 4) |
| Models | Gemini Flash (vision), Groq `openai/gpt-oss-120b` (text), bge-m3 (text embeddings), CLIP ViT-B/32 (photo embeddings) |
| Data | PostgreSQL 16 + PostGIS 3.6, Qdrant 1.20, Alembic migrations |
| Evaluation & ops | Ragas + DeepEval (eval), Langfuse (monitoring), Locust (load), pytest, ruff, GitHub Actions, gitleaks |
| Infra | Docker Compose, uv (Python 3.12) |

## Quick start (Windows, PowerShell)

**Prerequisites:** [Docker Desktop](https://www.docker.com/products/docker-desktop/), [uv](https://docs.astral.sh/uv/). Ports used: **5433** (Postgres), **6333** (Qdrant), **8025** (Mailpit), **8010** (API), **8501** (UI).

```powershell
# 1. Python 3.12 + dependencies into .venv
uv sync

# 2. Settings: copy the template, then add your two free API keys
copy .env.example .env

# 3. Start Postgres + PostGIS, Qdrant and Mailpit
docker compose -f infra/docker-compose.yml up -d db mailpit qdrant

# 4. Create the tables, then load the ward maps
uv run alembic upgrade head
uv run python scripts/load_wards.py

# 5. API on http://127.0.0.1:8010
uv run uvicorn app.main:app --reload --app-dir backend --port 8010

# 6. UI on http://localhost:8501 (in a second terminal)
uv run streamlit run ui/app.py
```

Health check: <http://127.0.0.1:8010/healthz?db=1> should show `{"ok":true,"db":{"select1":true,"postgis":true,"qdrant":true}}`. The Qdrant dashboard is at <http://127.0.0.1:6333/dashboard>.

> **Port notes:** Postgres runs on **5433** (not 5432) so it doesn't clash with a locally installed Postgres. Always use `127.0.0.1`, not `localhost`: on Windows, `localhost` tries IPv6 first and each call takes ~2 s.

## Environment variables

Copy `.env.example` to `.env`. **Sprints 1–2 need only the first two;** everything else has a working default.

| Variable | Needed | What it is |
|---|---|---|
| `GROQ_API_KEY` | now | Free key from [console.groq.com](https://console.groq.com) |
| `GOOGLE_API_KEY` | now | Free Gemini key from [aistudio.google.com](https://aistudio.google.com) |
| `TEXT_MODEL`, `VISION_MODEL` | optional | Model names; swap models here, not in code |
| `DATABASE_URL` | optional | Default `postgresql://postgres:dev@127.0.0.1:5433/civic` |
| `QDRANT_URL`, `QDRANT_API_KEY` | optional | Default local Docker; set both for Qdrant Cloud |
| `LANGFUSE_*` | Sprint 4 | Monitoring keys; tracing is off while empty |
| `PII_KEY` | Sprint 3 | Encryption key for personal data (command in `.env.example`) |
| `SUPABASE_*` | deploy | Only for the online demo |

**Never commit `.env`.** It's git-ignored, and CI runs gitleaks to catch leaked keys.

## Tests

```powershell
uv run ruff check .     # lint
uv run pytest -q        # 29 tests: API, errors, uploads, DB, Qdrant, UI (Streamlit AppTest)
```

DB and Qdrant tests are skipped automatically when those services aren't running.

## Project structure

```
backend/
  app/
    api/reports.py        POST /reports, dossier, media endpoints
    routing/engine.py     pin → ward → department (SQL)
    dossier/              bilingual complaint templates
    providers/fakes.py    simulated AI (replaced in Sprint 4)
    vectors.py            Qdrant: create collections, upsert, search
    config.py · db.py · main.py
  migrations/             Alembic: 0001 tables, 0002 vectors moved to Qdrant
  tests/
ui/                       Streamlit app: report screen, dossier, i18n (hi/en), theme
data/                     ward maps, roads, PWD list, departments, categories, rules (+ COVERAGE.md, DEPARTMENTS.md)
scripts/                  fetch/load wards, import OSM roads, match PWD names, reindex Qdrant
infra/                    docker-compose (Postgres+PostGIS, Qdrant, Mailpit), Dockerfiles
docs/                     PRD, requirements, UX, architecture, plan, bugs, test catalog, interview prep
```

## Documentation

| Doc | What's inside |
|---|---|
| [PRD](docs/PRD.md) | Problem, users, features (MoSCoW), success metrics |
| [REQUIREMENTS](docs/REQUIREMENTS.md) | Functional requirements, business rules, lifecycle |
| [UX](docs/UX.md) | Screens, flows, wireframes, look and feel |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | System design (§0 = latest simplifications) |
| [WORKFLOW](docs/WORKFLOW.md) | End-to-end flow, agents and engines |
| [PLAN](docs/PLAN.md) | 14 one-week sprints |
| [TECH-MEETING-01](docs/TECH-MEETING-01.md) | Decision log (latest changes at the top) |
| [TEST-CATALOG](docs/TEST-CATALOG.md) | 161 ways to break the system |
| [BUGS](docs/BUGS.md) | Bugs found and fixed |
| [Sprint 2 API contract](docs/contracts/sprint2-api.md) | Request/response shapes and error keys |

## Data sources and honesty notes

- **Ward boundaries:** Delhi MCD 2022 map (OpenCity, via the Wayback Machine). The archive is cut off, so 189 of 250 wards are present; all coverage wards are complete.
- **Roads:** © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors (ODbL).
- **PWD roads:** the official PWD Delhi "Roads with PWD" inventory. Being on the PWD list means PWD; for roads not on the list, road width decides.
- **Departments and rules:** researched from official sites, with a source link and date on every row. Unverified items are marked as such.
- The Kalindi Kunj ghat zone is **hand-drawn (approximate)**. Details are in [`data/COVERAGE.md`](data/COVERAGE.md) and [`data/DEPARTMENTS.md`](data/DEPARTMENTS.md).

## Limitations (v1)

- One pilot city (Delhi) and a limited set of coverage wards. Locations outside coverage get general helplines, not a guess.
- The citizen files the complaint themselves; the app has no authority over departments.
- Free-tier hosting and models: cold starts and rate limits are possible in the online demo.

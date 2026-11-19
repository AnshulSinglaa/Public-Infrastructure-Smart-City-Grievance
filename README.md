# Civic Grievance Router

> **Snap a photo, write a line in Hindi or English, and find out which government department owns that exact spot, the official rule that proves it, and a complaint ready to file.**

A **multimodal agentic RAG** application for civic complaints, with geospatial routing. **Pilot city: Delhi.** The code is city-agnostic: wards, roads, rules and contacts are data files.

![status](https://img.shields.io/badge/status-Sprint%206%20chat%20built-blue) ![python](https://img.shields.io/badge/python-3.12-blue) ![tests](https://img.shields.io/badge/tests-run%20uv%20run%20pytest-lightgrey)

---

## The problem

In Delhi, a pothole on a road **≥ 60 ft wide** belongs to **PWD**, but on a narrower lane it's **MCD**'s. Sewers belong to **DJB**, big trunk drains to **Irrigation & Flood Control**, and NDMC and the Cantonment run their own areas. Citizens don't know where to complain, and departments bounce complaints back ("not my department"). Existing portals (CPGRAMS, MCD 311, Swachhata) all assume you already know the right department.

## What Civic Grievance Router does

```
 photo + text (English / Hindi / Hinglish) + GPS
        │
        ▼
 understand the problem ──► find the owner of THAT spot ──► cite the rule ──► ready-to-file complaint
   (multimodal AI)            (geospatial SQL, PostGIS)       (RAG)            (+ call script, tracker)
```

The app **never files complaints on the citizen's behalf**: it routes and prepares, and the citizen files on the official channel. It **never guesses**: when it's unsure it asks, and when it doesn't cover an area it says so and shows the general helplines.

## Current status (Sprint 6: cited Q&A chat built, release hardening in progress)

| Done ✅ | Next 🔜 |
|---|---|
| **Real routing (SQL, PostGIS)**: 73 department rules (59 checked by scenario tests, 14 flagged with reasons), PWD road list, the divided-road rule (other carriageway on the PWD list), power company (discom) and I&FC drain lookups | **Deploy mini-sprint**: put the app online (HTTPS, email login, backups) |
| **Chat intake**: the agent's questions (main road or lane? which drain? which power company? which department?) appear as chat messages; answer with a button or free text in any language. The AI only maps text to the offered options; it **never picks the department** | Run the Q&A eval live (92-row golden set, harness built) and commit the first recording; set Supabase "Confirm email" to OFF so email + password sign-up works on the live demo (no SMTP needed) |
| **Real multimodal AI**: Gemini (`gemini-3.5-flash-lite`) reads the photo, Groq (`gpt-oss-120b`) reads Hindi/English/Hinglish text, in a **LangGraph agent** | After Sprint 6: research agent (government sites only) for places outside coverage |
| Emergency path (live wire, gas leak, open manhole, …) shows "call 112 / helpline first"; junk photos get a friendly retake | Sprint 7–8: duplicates, monsoon hotspot alerts, full evaluation, Locust |
| Prompt injection can't change the department or severity (tested with DeepEval against the real model) | Later: tracker + appeals, votes, final demo |
| Privacy: photos are **blurred before** they reach the AI; text is masked; the user is told which AI services see the report | |
| Outside Delhi: the state's official grievance portal (36 states/UTs, 29 verified) + CPGRAMS | |
| Langfuse tracing (masked), strict JSON checks, retries within a < 60 s budget, rate limits (incl. chat answers) | |
| **Tests and security:** backend + UI suites (`uv run pytest -q`), the live AI eval, CI green, 76 bugs tracked and most fixed, 9 security audit runs (SEC-001 to SEC-019 closed) | |

> ℹ️ From Sprint 5 the department comes from the 73 rules in PostGIS, not the simple ward lookup.

### Sprint 6: cited Q&A (home chat + per-report `/ask`)

Ask "who handles waterlogging in my town, and how do I report it?" and get a short, **cited** answer:

- **Hybrid retrieval:** Qdrant dense (Gemini embeddings) + BM25, fused with RRF, over an official-documents corpus built by an ingest job (`sources.yaml`; https and government hosts only, re-checked on every redirect). Postgres re-checks layer, approval and validity, so a wrong vector payload can't leak a rule.
- **Cite-or-drop:** the model returns a structured plan (responsible body, where to complain, steps). Every sentence must quote a stored passage or it is dropped, and numbers must appear in the quote.
- **Applicability:** each source has a `level` (central / state / local) and `covers` / `not_for`, so a central portal such as CPGRAMS is never offered for a local drain.
- **Local body for any town:** the official Local Government Directory (`local_body` table, 5,050 urban bodies) turns a town name such as "Gurugram" into its municipal body, shown as a general rule and never as a cited fact. Villages are not in the urban list (see Limitations).
- **Conversation memory:** place, issue and "it's my own problem" are kept per thread, so a follow-up keeps its context. A problem plus a question also offers **Start a report**.
- **Language rule:** the reply follows the question's language and script (English, Hinglish in Latin letters, Hindi in Devanagari), checked in code with one retry.
- **Free-tier survival:** Groq first, Gemini fallback, a per-model circuit breaker, a per-minute token throttle, Retry-After handling and a "busy, try again in N s" reply.
- **Privacy:** questions are masked (phones, emails, house numbers, Aadhaar-like numbers) before storage, tracing, embedding or any model call. Home-chat rows are encrypted at rest, and `DELETE /chat` removes a user's history.
- **Eval:** `eval/` holds a 92-row golden set and a scorer (right authority, right channel, language, no irrelevant numbers, follow-up memory, free-tier survival) plus Ragas and DeepEval judges. See [eval/README.md](eval/README.md). The first live run is still to do, so no accuracy number is claimed here yet.

## Architecture

```
 ┌──────────────────────────────────────────────┐
 │  Streamlit UI  (:8501)  English UI             │
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
| Models | Gemini Flash (vision), Groq `openai/gpt-oss-120b` (text) with a Gemini fallback via LiteLLM, Gemini embeddings + BM25 (retrieval), CLIP ViT-B/32 (photo embeddings) |
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

# 5. API on http://127.0.0.1:8010 (--reload is for local dev only; never use it when deployed)
uv run uvicorn app.main:app --reload --app-dir backend --port 8010

# 6. UI on http://localhost:8501 (in a second terminal)
uv run streamlit run ui/app.py
```

Health check: <http://127.0.0.1:8010/healthz?db=1> should show `{"ok":true,"db":{"select1":true,"postgis":true,"qdrant":true}}`. The Qdrant dashboard is at <http://127.0.0.1:6333/dashboard>.

> **Port notes:** Postgres runs on **5433** (not 5432) so it doesn't clash with a locally installed Postgres. Always use `127.0.0.1`, not `localhost`: on Windows, `localhost` tries IPv6 first and each call takes ~2 s.

## Environment variables

Copy `.env.example` to `.env`. **Locally you need the two AI keys and `PII_KEY`;** everything else has a working default (the Sprint 6 tunables are listed, commented out, in `.env.example`).

| Variable | Needed | What it is |
|---|---|---|
| `GROQ_API_KEY` | now | Free key from [console.groq.com](https://console.groq.com) |
| `GOOGLE_API_KEY` | now | Free Gemini key from [aistudio.google.com](https://aistudio.google.com) |
| `TEXT_MODEL`, `VISION_MODEL` | optional | Model names; swap models here, not in code |
| `DATABASE_URL` | optional | Default `postgresql://postgres:dev@127.0.0.1:5433/civic` |
| `QDRANT_URL`, `QDRANT_API_KEY` | optional | Default local Docker; set both for Qdrant Cloud |
| `LANGFUSE_*` | Sprint 4 | Monitoring keys; tracing is off while empty |
| `PII_KEY` | Sprint 3 | Encryption key for personal data (command in `.env.example`) |
| `APP_ENV`, `AUTH_MODE` | deploy | `local`/`dev` on a laptop; `prod`/`supabase` online (prod refuses to start with a missing or weak secret) |
| `SUPABASE_*`, `TURNSTILE_*`, `ALLOWED_ORIGINS` | deploy | Only for the online demo, see [DEPLOY.md](docs/DEPLOY.md) |

**Never commit `.env`.** It's git-ignored, and CI runs gitleaks to catch leaked keys.

## Deploy (free tier)

The live demo runs on **Render** (API, Docker: `infra/api/Dockerfile` + `render.yaml`), **Streamlit Community Cloud** (UI), **Supabase** (Postgres + PostGIS + email-code login), **Qdrant Cloud** and **Cloudflare Turnstile**, with GitHub Actions for CI, warm-up pings and encrypted daily backups. One command loads a fresh database (tables + all data): `uv run python scripts/load_all.py`.

Step-by-step checklist (accounts, which value goes in which dashboard, git steps): **[docs/DEPLOY.md](docs/DEPLOY.md)**.

## Tests

```powershell
uv run ruff check .     # lint
uv run pytest -q        # backend + UI tests: API, auth, privacy, limits, routing, chat, Q&A, ingest, DB, Qdrant, UI (Streamlit AppTest)
```

DB and Qdrant tests are skipped automatically when those services aren't running.

## Project structure

```
backend/
  app/
    api/reports.py        POST /reports, dossier, media endpoints
    routing/engine.py     pin → ward → road → rule → department (SQL); conditions.py = rule conditions
    intake/               LangGraph agent, chat events (events.py), emergency path
    dossier/              bilingual complaint templates
    llm/gateway.py        every text-model call: Groq -> Gemini, breaker, throttle, embeddings
    rag/                  retrieve.py (hybrid search), answer.py (one Q&A turn), slots.py, local_body.py, ingest.py
    guards/               input (injection, intent) and output (cite-or-drop) checks
    providers/fakes.py    scripted AI for tests (PROVIDER_MODE=fake)
    vectors.py            Qdrant: create collections, upsert, search
    config.py · db.py · main.py
  migrations/             Alembic 0001–0010 (tables, routing rules, geography indexes, deploy, chat_event, local_body)
  tests/
ui/                       Streamlit app: login, chat (chat.py), composer, location (GPS/map/landmark), dossier, My reports, English strings, theme
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
| [Sprint 3 API contract](docs/contracts/sprint3-api.md) | Login, pin, privacy rules, rate limits, new error keys |
| [Sprint 5 API contract](docs/contracts/sprint5-api.md) | Routing rules, chat questions, `/answer` and `/conversation` |
| [Deploy API contract](docs/contracts/deploy-api.md) | Email-code login, Turnstile, token handling, login limits |
| [DEPLOY](docs/DEPLOY.md) | Deploy runbook and your setup checklist |
| [REVIEW-S5](docs/REVIEW-S5.md) | Tech-lead review of Sprint 5 |
| [REVIEW-PROJECT](docs/REVIEW-PROJECT.md) | Whole-repo review after Sprint 6 chat (40 findings) |
| [FIX-PLAN-S6](docs/FIX-PLAN-S6.md) | Chat quality fix plan (answer planner, applicability, language, free tier) |
| [eval/README](eval/README.md) | Q&A evaluation: golden set, metrics, how to run |

## Data sources and honesty notes

- **Ward boundaries:** Delhi MCD 2022 map (OpenCity, public domain), **all 250 wards**. A complete mirror was verified byte-for-byte against the archived copy. **25 coverage wards**, chosen so every agency type appears at least once.
- **Roads, metro, rail, industrial areas, river:** © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors (ODbL).
- **State and UT boundaries (all 36):** [Natural Earth](https://www.naturalearthdata.com/) admin-1, public domain, simplified to ~1 km; used only to find which state a pin is in. Natural Earth shows de facto lines in Kashmir, so pins there get CPGRAMS only.
- **State grievance portals:** `data/state_directory.csv`, 29 of 36 verified on official `.gov.in` / `.nic.in` sites (date in each row); the rest fall back to CPGRAMS.
- **NDMC and Delhi Cantonment boundaries:** [DataMeet](https://github.com/datameet) (CC BY 4.0, approximate). Also used, together with the 250 MCD wards, for the "inside Delhi?" outline.
- **PWD roads:** the official PWD Delhi "Roads with PWD" inventory. Being on the PWD list means PWD; for roads not on the list, road width decides.
- **Departments and rules:** researched from official sites, with a source link and date on every row. Unverified items are marked as such.
- The Kalindi Kunj ghat zone is **hand-drawn (approximate)**. Details are in [`data/COVERAGE.md`](data/COVERAGE.md) and [`data/DEPARTMENTS.md`](data/DEPARTMENTS.md).

## Limitations (v1)

- One pilot city (Delhi) and a limited set of coverage wards. Locations outside coverage get general helplines, not a guess.
- The citizen files the complaint themselves; the app has no authority over departments.
- Q&A "local body" lookup covers **urban** bodies only (LGD: 5,050). A village or gram panchayat (for example Badhra, Haryana, which is not in that list) gets the general "your local body" answer, not a named one.
- Free-tier hosting and models: cold starts and rate limits are possible in the online demo.

# Workflow: Agents, Engines and Every Branch

> **2026-10-25: voice input removed.** Ignore `transcribe` / Whisper / voice-note steps below. Input = photo + text + GPS.
>
> A companion to ARCHITECTURE.md, in plain words. If the two disagree, ARCHITECTURE.md wins.

## 0. System at a glance

```
                     photo · text · GPS
   citizen ─────────────────────────────┐
                                        ▼
   ┌──────────────────────────────────────────────┐
   │  Streamlit UI  (:8501)                       │
   │  camera · map pin · Hindi / English          │
   └──────────────────────────────────────────────┘
          │ POST /reports               ▲ next step
          │ (+ answers)                 │ (ask / dossier)
          ▼                             │
   ┌──────────────────────────────────────────────┐      ┌──────────────────┐
   │  FastAPI backend  (:8010)                    │ tick │  GitHub Actions  │
   │  auth · validate · call modules              │◄─────│  cron, 30 min    │
   └──────────────────────────────────────────────┘      └──────────────────┘
          │ runs graph
          ▼
   ┌──────────────────────────────────────────────┐    text     ┌──────────┐
   │  Intake agent  (LangGraph)                   │────────────►│  Groq    │
   │  understand · ask user · draft letters       │   photo     ├──────────┤
   │  pauses when unsure (interrupt)              │────────────►│  Gemini  │
   └──────────────────────────────────────────────┘             └──────────┘
          │ calls (plain Python, no AI)
          ▼
   ┌──────────────────────────────────────────────┐
   │  Engines                                     │
   │  location · dedup · routing (SQL) · rag      │
   │  dossier · tracker · votes · privacy         │
   └──────────────────────────────────────────────┘
          │ read / write
          ▼
   ┌──────────────────────────────────────────────┐
   │  Postgres  (Supabase)                        │
   │  PostGIS : wards · roads · pins              │
   │  pgvector: rule chunks · photo vectors       │
   │  tables  : reports · issues · dossiers       │
   └──────────────────────────────────────────────┘
          │ dossier (complaint + rule + deadline)
          ▼
   citizen files it: portal · email · 📞 call
```

## 1. Who does what

The system has **1 AI agent** (a LangGraph graph with 5 AI steps) and **9 plain-code engines**. The AI steps only judge language and pixels. Departments, deadlines and helplines always come from data.

### The AI agent: intake graph (LangGraph)

| Step (node) | Model | Responsibility | Output |
|---|---|---|---|
| `transcribe` | Groq Whisper-large-v3 | Hindi/English/Hinglish voice → text | transcript + language |
| `assess_image` | Gemini Flash | Category, junk check, road width (wide road vs gali), drain type (sewer vs nala), face/plate boxes | `ImageAssessment` JSON |
| `assess_text` | Groq Llama | Category, severity, landmark from the text/transcript | `TextAssessment` JSON |
| `draft` | Groq Llama | Writes the "why" sentences, complaint prose and letters, **only from retrieved rule chunks** | `{claim, citation_id, quoted_span}` list |
| `compare` (resolution) | Gemini Flash | Before/after photo comparison, **advisory only** | looks_fixed / not_fixed / unclear |

**Interrupts (the agent pauses and asks the user):** clarify category · confirm pin · confirm duplicate join · one-tap issue question (road / drain / ghat) · pick between candidate departments.

### The plain-code engines

| Engine | Responsibility |
|---|---|
| `fuse` | Merges the image and text results; confidence < 0.7 or a mismatch → ask |
| `location` | GPS drift check, pin seed (best accuracy), landmark geocoding |
| `dedup` | Same category within 50 m + CLIP image similarity ≥ 0.85 → candidate list; the user confirms |
| `routing` | SQL: ward → special zone → road snap (25 m) → PWD list → rule → confidence |
| `rag` | Hybrid search (full-text + vectors), pinned rule citations, verbatim span check (failing claims dropped) |
| `dossier` | Bilingual complaint, call script, email draft, share page + PDF/QR |
| `tracker` | Master clock, "day X of Y", escalation ladder L0–L3, rejection → evidence letter / re-route |
| `resolution` | After-photo 30 m check, vote rule by reporter count |
| `notify` + `privacy` + `jobs` | Check-in cards, self-reminder email, encryption/masking/blur, a `/jobs/tick` cron every 30 min |

## 2. Intake flow (upload → dossier)

```mermaid
flowchart TD
  U[Citizen uploads: photo, voice, text, GPS] --> Q{Photo quality ok?}
  Q -- too dark/blurry --> RT[Ask: better light, retake] --> U
  Q -- ok / user continues --> A[AI: transcribe + assess_image + assess_text]
  A --> F{fuse}
  F -- junk --> J[Polite reject, retake]
  F -- out of scope --> OS[Show general helplines]
  F -- conf < 0.7 or mismatch --> C[/Ask user: pick category/] --> P
  F -- ok --> P[/Confirm pin: map click or landmark/]
  P -- GPS drift: accuracy > 50 m, moving --> W[Warn + must confirm] --> P
  P --> D{Duplicate within 50 m?}
  D -- yes --> JN[/Same issue?/]
  JN -- join --> M[Join master: count + 1]
  JN -- resolved < 30 days --> RO[Offer Reopen]
  JN -- no --> R
  D -- no --> R[Routing SQL]
  R -- outside covered wards --> OC[Not covered yet: general helplines]
  R -- evidence unclear and depts differ --> IQ[/One-tap: road, drain or ghat?/] --> R
  R -- rule confidence < 0.7 --> PC[/Pick between 2 departments/] --> DR
  R -- decided --> DR[AI draft from retrieved rules]
  DR --> V{Span check}
  V -- claim fails --> X[Drop claim; if ownership dropped: no cited rule found + suggested deadline]
  V -- ok --> DS[Dossier ready]
  X --> DS
  DS --> FL[Citizen files: portal, email draft, Call mode, family share, PDF]
```

## 3. After filing (tracker → outcome)

```mermaid
flowchart TD
  F[Filed: master clock starts] --> T[Tracker: day X of Y + check-in cards day 3, 7, deadline]
  T -- not filed yet --> N1[Day 1 and 3: Did you file it?]
  T -- says fixed --> AP{After-photo within 30 m?}
  AP -- no --> MC[Move closer]
  AP -- yes --> VR{Vote rule}
  VR -- R=1: their photo --> RS[Resolved]
  VR -- R=2: both say fixed --> RS
  VR -- R≥3: majority + 2 fixed --> RS
  VR -- fails or tie --> RE[Reopened, clock continues]
  VR -- undecided at day 14 --> SU[Status unknown]
  T -- deadline over --> OD[Overdue: L0 reminder + L1 appeal unlock]
  OD --> L2[L2 CPGRAMS draft after wait]
  L2 --> L3[L3 RTI draft, 30-day legal reply]
  T -- dept says not ours --> RJ{Rule says it's theirs?}
  RJ -- yes --> EL[AI evidence letter citing the rule]
  RJ -- no --> RR[Re-route: new dossier, clock restarts, history kept]
  T -- 14 days silent after deadline --> SU
  SU -- any check-in or passer-by --> T
  RS -- issue back within 30 days --> RO[Reopen]
```

## 4. Background jobs

`GitHub Actions cron (every 30 min)` → `POST /jobs/tick` → `advance(master, now)` for each open master (overdue, status unknown, vote close) → create check-in cards → optional self-reminder emails (quiet hours 22:00–07:00 IST) → purge expired drafts, voice clips and PII. The same function runs on every read, so it is idempotent.

## 5. One worked example

Ramesh, 10pm, Sarita Vihar, Hindi voice note about sewage overflow, dark photo, GPS ±120 m:
1. **Quality check:** too dark → "move to better light" → he submits anyway, so the voice note becomes the main input.
2. **AI:** transcript → category = waterlogging/sewage, severity high, landmark "school ke saamne".
3. **Pin:** accuracy 120 m → warning → landmark geocoded → he confirms.
4. **Dedup:** the dark photo can't match images → location + category candidates → "Is this the same?" → joins (count 3).
5. **Routing:** drain type unclear → one tap: "Big open nala or sewer/manhole?" → sewer → DJB rule.
6. **Draft:** DJB charter clause retrieved and quoted. A deadline is cited if found, otherwise the suggested 15 days.
7. **Filing:** Call mode plays the Hindi script → he taps "Call 1916".
8. **Day 6:** someone at DJB says "not ours" → the rule says sewer = DJB → evidence letter.
9. **Day 20:** overdue → appeal draft (L1) unlocks.

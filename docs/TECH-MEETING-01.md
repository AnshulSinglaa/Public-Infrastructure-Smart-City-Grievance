# Tech Meeting 01: Requirements & Stack

**Date:** 2026-10-24 · **Attendees:** tech-lead, backend-developer, frontend-developer, devops-engineer, qa-engineer · **Input:** PRD.md (incl. §14), REQUIREMENTS.md, UX.md
**Status:** Proposals. Items in §5 need the user's decision before ARCHITECTURE.md is written.

---

## 1. Agreed by everyone

| Area | Decision | Why |
|---|---|---|
| Shape | **Modular monolith**: one API, one DB, no microservices | Solo developer, free tiers |
| Routing | **Deterministic SQL**: pin → ward → special zone → road snap → PWD list → rule. **No LLM picks the department.** | Testable, explainable, matches the "proof, not opinion" principle |
| DB | **Postgres 16 + PostGIS + pgvector**, one database for geo, vectors and state | Corpus is small (~15 docs); PostGIS is needed anyway |
| Vector DB | **Drop Qdrant** | It would add a service and buy nothing at this size; the Qdrant free tier also suspends when idle |
| API | **FastAPI (Python)** + Pydantic at every boundary | Ragas, CLIP and OpenCV are all Python |
| Frontend | **React + Vite + TypeScript PWA**, Leaflet + OSM tiles, a simple JSON i18n helper | Free; web push needs a service worker |
| Image dedup | **open_clip ViT-B/32 on CPU**, embeddings stored in pgvector; the **user always confirms the join** | Similarity only ranks candidates; the user is the final check |
| Citations | Stored on the rule (`source_citation_ids`). RAG is used for the "why" text, evidence letters and appeals. Claims are generated as structured `{claim, citation_id}` objects, so uncited text cannot appear. | Zero uncited claims, by construction |
| PII | Fernet field encryption (key in env) + an HMAC column for phone/email lookup | Meets M8 |
| Auth | **Email OTP or Google sign-in.** No phone OTP (SMS costs money) | Free |
| Email | **The app never sends.** Mailpit locally; a gov-domain block guard is tested in CI | NFR-DEMO-1 |
| Mocks | AI behind interfaces; scripted fakes and recorded fixtures; MSW + a `?scenario=` switch in the frontend; Playwright with fake GPS, camera and mic | Every UX branch can be tested without spending API calls |
| Eval | Ragas as a manual or weekly job, never on every push; cache LLM responses | Free-tier rate limits |
| iOS | Best effort only (personas use Android) | Web push on iOS needs an installed PWA |

## 2. Where the team disagreed, and the recommendation

| Topic | Options | Recommendation |
|---|---|---|
| **LangGraph** | Tech-lead: skip it, the lifecycle is already a state machine. Backend: use it only for the intake graph (its interrupt fits "ask the user"). | **Small LangGraph graph for intake + clarify + letter drafting only.** Routing stays SQL. It keeps the project honestly "agentic" where an agent actually decides something. *User decides.* |
| **Models** | Tech-lead: one Gemini Flash call for everything, including STT. Backend: Groq Whisper STT + Gemini vision + Groq text. | **Groq Whisper-large-v3 for STT**, **Gemini Flash for vision**, **Groq Llama for drafting.** Benchmark STT against Sarvam on ~30 of our voice clips and keep the better one. The Gemini free tier may train on prompts, so use only test data there. *User decides.* |
| **Embeddings** | gemini-embedding-001 vs local bge-m3 | **bge-m3 locally** (free, private, handles Hindi). Gemini only if the laptop is too slow. |
| **Hosting DB/storage** | Supabase (DB + storage + auth, but pauses after 7 idle days) vs Neon + Cloudflare R2 | **Supabase**: one vendor, with built-in auth and signed URLs. Add a keep-alive cron and a `pg_dump` backup. |
| **Scheduler** | APScheduler in-process vs a cron "tick" endpoint | **Compute deadlines on read** ("day X of Y" needs no job). Nudges and retention purges run through an idempotent `POST /jobs/tick` called by GitHub Actions cron. APScheduler dies when the free host sleeps. |
| **PDF** | Print CSS vs headless Chromium (Playwright) | **Print CSS first** (the browser shapes Devanagari correctly for free). Move to Playwright only if a server-side PDF is needed. |
| **Ward count** | 20 now vs 8 first | **Start with 8** (Kalindi Kunj ghat area, PWD roads, a boundary edge), then grow toward 20. The data work is the real cost. |

## 3. Proposed values for the ⟨TBD-ARCH⟩ items

| Item | Value |
|---|---|
| Photos per report | 3, compressed client-side to ≤1280 px |
| Dedup image similarity | CLIP cosine **≥0.85** to start; tune on the **dev set** (not the test set) |
| Road snap | **25 m** (one value used everywhere; the 15 m in §7.1 is a typo) |
| Boundary buffer | **15 m** (ward edges and two-segment roads) |
| Resolved masters matchable for | 30 days |
| GPS "location may be off" | `accuracy > 50 m` OR `speed > 3 m/s` OR two fixes 3 s apart differ by >30 m |
| Photo quality (client) | Mean brightness <60/255 = too dark; Laplacian variance <100 = blurry |
| Resolution distance check | Uses `distance − GPS accuracy ≤ 30 m`, so genuine photos aren't rejected |
| Face/plate blur | Pillow Gaussian blur on detected boxes. **Fails closed**: no boxes or an error means the photo is left out of shared outputs |
| Dossier format | JSON record → HTML share page (unguessable token) → print-CSS PDF + QR |
| Routing confidence | `rule.confidence × evidence × citation × staleness`, **needs rework** (see §4, Q-6) |

## 4. Doc gaps and contradictions found (QA + frontend). Business-analyst to fix in REQUIREMENTS.md and UX.md

| # | Issue | Proposed resolution |
|---|---|---|
| Q-1 | Vote rule: with R=2, the AI plus one vote resolves the issue, which makes the AI decisive and breaks "advisory" | With R=2, **both reporters** must confirm; the AI is never counted as a vote |
| Q-2 | Vote: 7-day window vs day-14 cut-off overlap; R changes during the vote | Freeze R when the after-photo is uploaded; at day 7 with too few votes, keep waiting until day 14, then mark **status unknown** |
| Q-3 | After-photo >30 m: UX says "take anyway, marked far"; REQ rejects it | **Reject** (follow REQ). The UX shows "Move closer" |
| Q-4 | No-rule deadline: the UX flow shows none; E05/FR-4.6 show a suggested one. Hindi string differs (आधिकारिक vs सरकारी) | Show the suggested deadline everywhere; the single string is **"कोई आधिकारिक नियम नहीं मिला"** |
| Q-5 | Escalation: UX shows a 15-day wait before L1; REQ unlocks L1 together with L0 | Follow REQ §6, and update the UX |
| Q-6 | Confidence maths: MCD rule × inferred evidence = 0.68 → always asks; the ghat rule (0.6) always asks "main road or lane?", which makes no sense for a ghat | Recalibrate the factors on the dev set; **the question must match the issue type** (road: "main road or lane?"; drain: "big open nala or sewer line/manhole?"; ghat: "on the river bank or in the water?") |
| Q-7 | **Sewer vs storm drain** decides DJB vs PWD vs MCD, and nothing in intake captures it | Add an intake field (the vision model tags manhole/sewer vs open nala) plus the drain question from Q-6 |
| Q-8 | Dedup: FR-6.1 never matches resolved masters; FR-6.6 matches them for 30 days | Match them for 30 days and offer "**Reopen?**" |
| Q-9 | Offline: UX E07 queues the report; REQ Q3 requires connectivity | **Online-only v1.** The draft is saved in IndexedDB with a manual resume |
| Q-10 | Clocks: the deadline is per Report, but overdue and status unknown are Master states | The **master's clock starts at its first "filed" date**; later reporters inherit it |
| Q-11 | Re-route after "not ours, DJB's": the clock and earlier escalation steps are undefined | Re-route **restarts the clock** for the new department; the old steps stay in history; the evidence letter is offered first if the rule says it is theirs |
| Q-12 | C1 rule flag: 3 co-reporters on one master trigger the "≥3 distinct users" alone | Count **distinct masters**, not users |
| Q-13 | No "filed" tap → the report sits in dossier_ready forever with no nudges | Nudge at day 1 and day 3: "Did you file it?" |
| Q-14 | Dark photo fails similarity → a duplicate master is created | Low-quality photo → fall back to a location + category candidate list, and the user confirms |
| Q-15 | Phase tags: P1 acceptance blocks test S1/S2/S5/C3; FR-4.6 [P1] depends on FR-7.2 [P2] | Split the acceptance blocks by phase; move the needed FR-7.2 config to P1 |
| Q-16 | **The P1 demo has no filing path for low-literacy users** (Call mode is P2) | **Move S6 Call mode to P1**. It is cheap (tel: link + TTS script) and core to persona Ramesh |
| Q-17 | Hindi PII masking misses phone numbers spoken as words or Devanagari digits | Normalise Devanagari digits and number-words before masking; add test cases |
| Q-18 | Wrong references: FR-3.7 should be FR-3.11 (rejected), FR-3.5 should be FR-3.7 (two candidates); stale note at UX §4.4 | Fix |
| Q-19 | Things the web can't do: volume-key shutter; read-aloud continuing after the dialer opens; long Hindi `mailto:` bodies | Drop the volume key; Call mode **plays the script, then "Call now"**, with the script kept on screen; `mailto:` fills the subject only, plus a "Copy body" button |
| Q-20 | The PIN source is undefined (shutter GPS vs submit GPS) | Seed the pin from the **best-accuracy fix since the camera opened**; the user confirms |

## 5. Decisions needed from the user

1. **LangGraph:** small graph for intake, clarify and drafting (recommended), or none at all?
2. **Models:** Groq Whisper + Gemini vision + Groq text (recommended)? Is it OK to use the Gemini free tier with test data only?
3. **Wards:** start with 8, then grow (recommended), or 20 from day one?
4. **Hosting:** run the eval locally and demo on the free tier with a warm-up (recommended), or local-only?
5. **Second labeller (W1):** who? Blind labelling needs a real second person (a friend or classmate).
6. **Q-16:** move Call mode into Phase 1 (recommended)?

### ✅ User decisions (2026-10-24): all 6 recommendations accepted
1. A small LangGraph graph for intake, clarify and letter drafting only. Routing stays in SQL.
2. Groq Whisper-large-v3 for STT (benchmarked against Sarvam), Gemini Flash for vision, Groq Llama for text. The Gemini free tier is used with test data only.
3. Start with 8 wards, then grow toward 20.
4. Free-tier demo with a warm-up. The eval runs locally.
5. The user will find a second labeller.
6. S6 Call mode moves into Phase 1.
The disagreements in §2 are settled as recommended (bge-m3 local embeddings, Supabase, compute-on-read + tick cron, print-CSS PDF). All §4 resolutions (Q-1 to Q-20) are accepted.

### ✅ Change (2026-10-28, user): **English-only UI + UI v2 redesign**
The interface is English only (no Hindi toggle or bilingual labels). Users can write in English/Hindi/Hinglish and their text is kept as written; the complaint letter is English. The UI is redesigned to a chat-app look (slim sidebar with New report / My reports / Recent / user; one composer card). Spec: UX.md §14, which overrides earlier UX language and i18n rules and the hi/en strings in REQUIREMENTS.

### ✅ Change (2026-10-28, user): **eval, monitoring, performance tools**
- **Evaluation:** **Ragas** (faithfulness, context precision/recall on citations) + **DeepEval** (LLM test cases inside pytest: cites a real rule, no invented helpline or deadline, asks when unsure, refuses prompt injection). Wired in with the eval harness (Sprint 8); DeepEval smoke tests run in CI on recorded responses.
- **Monitoring:** **Langfuse** (Cloud free tier): one trace per report covering every AI call (latency, tokens, cost, prompt version, errors), with eval scores attached. Wired in with the real AI (Sprint 4). Keys go in `.env`; tracing is off when they are empty. **PII is masked before any trace is sent.**
- **Performance:** **Locust** load tests against the API (p50/p90, error rate) before the go/no-go and the final demo (Sprint 8 and 14).

### ✅ Change (2026-10-28, user): **Qdrant replaces pgvector**
Vectors (rule chunks + photo embeddings) move to **Qdrant**; Postgres + PostGIS keep everything else, including the chunk text used for quoting. Local: Qdrant in Docker. Demo: Qdrant Cloud free tier + keep-alive + re-index script. This overrides ADR-2 and the "Drop Qdrant" row in §1. Details are in ARCHITECTURE §0.

### ✅ Change (2026-10-29): **vision model = gemini-3.5-flash-lite** (gemini-2.5-flash is retired for new users; flash-lite was faster and more consistent than 3.5-flash on our key, ~17–19 s on the free tier)

### ✅ Decided (2026-10-29, user): **two extra features**
- **Night-safety priority (Sprint 5, with real routing):** a streetlight / dark-street / open-manhole report gets one severity level higher when it is (a) reported or photographed at night (19:00–06:00 IST, or the vision model sees darkness) AND (b) within 150 m of a school, metro station, bus stop, hospital or market (OSM points of interest for the 25 wards). The dossier says why ("near a metro exit, at night"). Rule-based, no AI decides it.
- **Monsoon hotspot alerts (Sprint 7, with duplicates):** known flood spots = the officially published Delhi waterlogging hotspot list (PWD / Traffic Police, with source + year) + our own past waterlogging reports (≥ 3 within 50 m). A pin near a hotspot shows "This spot is a known waterlogging point (source)", and the letter cites the history. Before the monsoon (June) a banner lists hotspots in the user's ward. Sources are always shown; past reports are counted only, never shown with personal data.

### ✅ Decided (2026-10-29, user): **outside-coverage help (2 layers)**
- **Layer 1 (Sprint 4–5):** a curated **state directory**: pin → state (state boundaries) → that state's official grievance portal + helpline + CPGRAMS. No AI, always correct. Replaces the bare "outside Delhi" card.
- **Layer 2 (after Sprint 6, reuses RAG cite-or-drop):** a **research agent** with a web-search tool restricted to official domains (`*.gov.in`, `*.nic.in`) that finds the likely local office for the town + problem type. Every claim needs a source link, otherwise it is dropped. Labelled "AI-researched, not verified". Web text is treated as data, never instructions. Results are cached per town + category and evaluated with DeepEval on a fixed set of non-Delhi towns. Uses a search API, not a full browser.

### ✅ Decided (2026-10-29, user): **conversational timing**: Sprint 5 = chat intake (the agent asks its questions as chat messages; the user answers with buttons or free text in any language; the dossier appears as a chat card). Sprint 6 = follow-up Q&A answered only from cited rules + the user's own ticket (e.g. "what if it's rejected?" → answer + [Draft appeal]). Routing stays in SQL; chat never decides the department.

### 📌 Pending (2026-10-28, user): **make it conversational**
The product must become a **conversational, multimodal agentic system**. *How* (chat intake, follow-up RAG Q&A, chat check-ins, what to keep or drop) will be decided later by the user. Until then, build nothing for it, but don't design anything that blocks a chat UI on top of the same LangGraph interrupts.

### ✅ Change (2026-10-27, user): **all departments, all problem types, 25 wards**
- Coverage grows from 8 to **~25 wards**, chosen so every Delhi agency type appears at least once.
- The app must route **any** civic problem, not just the 4 categories. The new source of truth is a department registry (`data/departments.csv`) + category taxonomy (`data/categories.csv`) + routing rule draft (`data/rules_draft.csv`), which overrides PRD §1/§5 "4 categories".
- A full "break the system" catalog (`docs/TEST-CATALOG.md`) drives QA and the eval set.
- Anything we still can't route gets the honest `out_of_scope` / `general_channels` path (CPGRAMS, 311). The app never guesses.

### ✅ Change (2026-10-25, user): **no voice input**
Input is photo + text (Hindi/English/Hinglish) + GPS. This drops Groq Whisper, the voice note UI, voice retention and the voice clips/STT benchmark from the eval. "Multimodal" now means **image + text + location**. The Hindi read-aloud output (Call mode script) is unchanged. ARCHITECTURE §0 has the details.

### ✅ Change (2026-10-24, user): frontend = **Streamlit** (replaces React + Vite PWA)
The FastAPI backend stays, and Streamlit is a thin UI calling the same API. Downgrades this causes, with their workarounds:

| Lost | Workaround |
|---|---|
| Live ghost-overlay camera (S3) | The original photo is shown above `st.camera_input` with a "same angle" hint, followed by the same 30 m + vote checks |
| Built-in geolocation | A small JS component (streamlit-js-eval / streamlit-geolocation) plus a click-to-pin map (streamlit-folium) |
| Web push (S7) | Pending check-in cards on login/home, plus an optional reminder email to the citizen (sandbox in the demo) |
| Browser Hindi TTS (C4) | Server-generated audio (e.g. gTTS) played via `st.audio` |
| Installable / offline | Not supported; the draft is kept in session |
| MSW frontend mocks | Backend fakes + demo-replay mode; Playwright still drives the UI |

## 6. Eval set (QA minimum)

- **Test set of 100 (frozen + hashed) plus a separate dev set of 50 for tuning.** Never tune on the test set.
- Labels are blind: the second person labels departments from official sources first and freezes them. Rule and chunk IDs are filled in a later pass.
- Coverage: ≥30 should-ask cases, ≥20 boundary roads, ≥20 sewer vs storm drain, ≥20 night/blurry, ≥15 per language, 50 junk + 50 valid images, ≥60 dedup pairs (including 45–55 m hard negatives), ≥20 before/after pairs.
- Voices come from real speakers (not TTS, not the developer).
- Measure kappa (inter-labeller agreement) on ≥20 cases. Add a correctness metric next to Ragas faithfulness. Run Ragas 3 times and report the variance.
- At n=100, 85% accuracy carries ±7 points of uncertainty. Report it honestly.

## 7. Phase plan (one developer, full-time weeks)

| Phase | Content | Effort |
|---|---|---|
| Data (runs in parallel throughout) | 8 → 20 ward polygons, OSM roads, PWD list matched to OSM, rules, contacts, 12–15 corpus docs, eval sets | ~2 wk |
| **P1 Must (+S6 Call mode)** | skeleton/auth/DB · intake + junk filter · location + routing · citations + dossier · dedup · privacy · eval harness | ~6.5 wk |
| **P2 Should** | S1/S2 · S4 tracker + ladder · S5 · S7 nudges · S3 ghost overlay + vote | ~3.5 wk |
| **P3 Could** | C1 · C2 map (sample data) · C3 · C4 · C5 | ~1.5 wk |
| **Total** | | **~13–14 weeks**; go/no-go for P2 at week 8 |

## 8. Risks (top)

1. **Road-level ground truth** is manual (PWD names → OSM geometry; OSM doesn't record road width). The ≥85% target depends on curation and the one-tap question.
2. **Hindi corpus quality**: scans and legacy KrutiDev fonts break text extraction, so OCR + a manual check are needed.
3. **Free-tier caps**: about 100 cases × 5 calls plus Ragas judge calls. Cache everything, keep model names in config, and run the eval in resumable batches.
4. **Demo cold starts**: Render free sleeps (~60 s to wake). Warm it up 10 minutes before the demo, keep a "demo replay" cache mode, and have a recorded backup video.

## Next

User answers §5 → business-analyst applies §4 fixes → tech-lead writes **ARCHITECTURE.md** → project-manager writes **PLAN.md**.

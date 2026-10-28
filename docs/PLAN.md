# PLAN: Civic Grievance Router

| | |
|---|---|
| **Author** | Project Manager · 2026-10-24 |
| **Inputs** | ARCHITECTURE.md (§0 Simplicity pass wins), TECH-MEETING-01.md §5 + §7, PRD.md §5 + §14 (W7 build order) |
| **Team** | One final-year student, full-time weeks, Windows laptop, free tiers. "Owner" below is the hat you wear that week, not a different person |
| **Calendar** | Week 1 starts Mon 26 Oct 2026 · week 14 ends Fri 29 Jan 2027 |

---

## 1. Goal and "done" for v1

**Goal:** a citizen gives a photo plus a voice note or text. The app works out what the problem is and where it is, which department owns it (with a cited rule), and hands back a complaint dossier that the citizen files themselves.

**v1 is done when both of these are true:**
1. **The 3-scenario demo runs end to end** on the hosted app (or locally, with a phone video as backup):
   - **A. Kalindi Kunj ghat:** idol debris photo + Hindi voice → garbage dump, high severity → riverbank zone rule → cited NGT rule + deadline → dossier.
   - **B. PWD pothole + Call mode:** drifting GPS → landmark text → pin confirmed → road ≥ 60 ft → PWD (cited) → "📞 Call 1908" + Hindi script read aloud.
   - **C. Kamla ji joins:** streetlight in a colony lane → dedup finds the existing master issue → "me too" → "N citizens reported".
2. **An eval report** (`eval/reports/<date>.md`) on the frozen 100-case test set: accuracy per stage (intake, location, routing, citation, end-to-end), Ragas run once, labeller agreement % on 20 cases, and the honest ±7-point note.

---

## 2. Timeline at a glance

```
 ┌──────────────── PHASE 1: Must + S6 Call mode ────────────────┐  ┌──── PHASE 2: Should ────┐ ┌ P3 ┐ ┌ end ┐
 │ W1   W2   W3   W4   W5   W6   W7   W8                        │  │ W9  W10  W11  W12       │ │W13 │ │ W14 │
 │ setup└─thin slice─┘ intake routing RAG+ dedup  privacy+eval  │──▶ tracker ladder S7  S3  │─▶Could│─▶final│
 │      (all fakes)   └──── real parts replace fakes ────┘      │  │ S1/S2  S5          votes│ │if  │ │eval │
 └──────────────────────────────────────────────────┬───────────┘  └─────────────────────────┘ │time│ │demo │
                                                    │ end of W8                                └────┘ └─────┘
                                           ┌────────▼────────┐  GO ──▶ Phase 2 as planned
                                           │  GO / NO-GO     │
                                           └────────┬────────┘  NO-GO ─▶ W9–10 fix P1, cut list (§8)
 ┌──────────────── DATA TRACK (parallel, from W1) ─────────────────────────────────────────────────────────┐
 │ 8 wards + zones → OSM roads → PWD list matching → rules + contacts → 12–15 corpus docs → dev 50 / test 100 │
 │                                                              test set FROZEN end of W6 ──▶ used only in W8 + W14 │
 └─────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Sprints (1 week each)

Every week ends with a **demo check**: you click through it yourself. If it doesn't work, the next week starts by fixing it.

### Phase 1: Must + S6 Call mode (W1–W8)

| Wk | Goal | Tasks | Demo at end of week | Owner |
|---|---|---|---|---|
| **W1** 26 Oct | Skeleton runs | 1. Repo layout (ARCH §15), `uv`, `.env.example` · 2. `docker-compose` db (PostGIS + pgvector) + Mailpit · 3. FastAPI `/healthz?db=1` + one `Settings` class · 4. Streamlit hello page calling the API · 5. CI: ruff + pytest + gitleaks | `docker compose up`, the API and the UI both start, and the CI badge is green | devops |
| **W2** 2 Nov | Thin slice, part 1 | 1. First alembic migration with **all** tables (P2 tables too) · 2. `POST /reports` (photo + text) · 3. **Fake** intake provider (fixed category/severity/confidence) · 4. **Hard-coded** route (always "MCD, ward X") · 5. Dossier template (en) shown in Streamlit | Upload photo + text → fake answer → dossier on screen | fullstack |
| **W3** 9 Nov | Thin slice, part 2 | 1. Pin: `streamlit-js-eval` GPS + `streamlit-folium` click-to-pin · 2. Real ward lookup (point-in-polygon on the 8 wards) · 3. Supabase email OTP login (`AUTH_MODE=fake` locally) · 4. Simple JSON cache for provider calls · 5. Scenario A scripted with fakes | Scenario A clicks through end to end on fakes, with a real ward | fullstack |
| **W4** 16 Nov | Real intake | 1. LangGraph intake graph (ARCH §3) · 2. Groq Whisper STT + Groq Llama text · 3. Gemini Flash vision (category, junk, road vs gali) · 4. Fuse → confidence; < 0.7 → ask the user (graph interrupt) · 5. Junk rejected with a reason | A real Hindi voice clip + photo gives the right category, and a selfie is rejected | backend-developer |
| **W5** 23 Nov | Real routing + location | 1. Road snap (25 m) + PWD list match + special-zone polygons · 2. Image tie-break + one-tap "Main road or inner lane?" · 3. Confidence + alternatives · 4. GPS drift rules + Nominatim landmark geocode · 5. `test_routing.py` on seed geometry | Scenario B routes to PWD from a landmark, with no GPS | backend-developer |
| **W6** 30 Nov | Cited rules + full dossier | 1. Ingest corpus (PyMuPDF + Tesseract hin), bge-m3 vectors · 2. Retrieval = the rule's pinned citations, then pgvector top-5 · 3. Claim generation + verbatim span check; "no cited rule found"; "Suggested deadline" label · 4. Bilingual complaint, email draft, deep link · 5. Share page + print CSS + QR | Scenario A dossier shows the NGT citation (doc + section) in Hindi + English | backend-developer |
| **W7** 7 Dec | Call mode + dedup | 1. S6: `tel:` link button + large-text Hindi script + gTTS playback · 2. Dedup: 50 m + category + CLIP similarity → candidate list → user confirms · 3. "Me too" join + reporter count in the dossier · 4. Hosted-mode fallback (location-only dedup) · 5. Big-button `style.css` | Scenarios B (with Call mode) and C both work locally | fullstack |
| **W8** 14 Dec | Privacy, eval, deploy → **go/no-go** | 1. One Fernet key; phone encrypted; masking in all outbound text · 2. OpenCV face blur (on error, don't share the photo) · 3. One `send_email()`, only to the user's own address · 4. Eval harness: `run.py` on dev, then the **first test-set run** · 5. Deploy API (Render) + UI (Community Cloud) + tick/keepalive crons | All 3 scenarios run on the hosted URL, and the first eval report exists | qa + devops |

**Go/no-go (Fri 18 Dec).** GO if all 3 scenarios run, the eval report exists, and routing accuracy on test is at least 70%. (The 85% target is the v1 goal, not the gate.) NO-GO means: spend W9–W10 fixing the weakest stage (the eval shows which one fails first), skip Phase 3, and apply the cut list.

### Phase 2: Should (W9–W12)

| Wk | Goal | Tasks | Demo at end of week | Owner |
|---|---|---|---|---|
| **W9** 21 Dec | S1/S2 + tracker core | 1. `camera_input` = verified, uploader = "unverified location" · 2. Dark/blur check before submit ("retake / flash") · 3. Lifecycle `advance()` + "day X of Y" · 4. `/jobs/tick` idempotent + test | A dark photo asks for a retake, and the tracker shows "day 3 of 15" | fullstack |
| **W10** 28 Dec | S4 ladder + S5 | 1. Ladder L0–L3: reminder → first appeal → CPGRAMS draft → RTI draft · 2. "Dept rejected" → evidence letter citing the ownership rule, or re-route · 3. `test_lifecycle.py` | Scenario B: "Dept rejected" produces the PWD evidence letter | backend-developer |
| **W11** 4 Jan | S7 nudges + S3 | 1. Pending check-in cards on login/Home (`/me/prompts`) · 2. Optional self-reminder email (Mailpit/sandbox), quiet hours, caps · 3. Re-shoot: original photo shown above the camera + 30 m check · 4. Gemini compare (advisory) | A "Did anyone come?" card appears, and the after-photo check runs | fullstack |
| **W12** 11 Jan | Votes + grow data | 1. Vote rules W5 (1 / 2 / 3+ reporters, 14 d → "status unknown") + `test_votes.py` · 2. Passer-by "Is this still here?" · 3. Add wards toward 20 if the data track is ahead · 4. Re-run eval on dev | Scenario C: two reporters vote "fixed" → resolved | backend-developer + data |

### Phase 3: Could (W13, only if time remains) and the final week

| Wk | Goal | Tasks | Demo | Owner |
|---|---|---|---|---|
| **W13** 18 Jan | Cheapest Could items first | 1. C5 copy-tweet button · 2. C4 Hindi status readout (gTTS already exists) · 3. C3 image vs voice mismatch → clarify question · 4. C1 outcome report → flag rule · 5. C2 ward map on data labelled "sample" | Each one done is shown, and the rest are moved to the backlog | fullstack |
| **W14** 25 Jan | Ship | 1. **Final test-set run** + eval report · 2. `warmup.sh` + record the 3-scenario backup video · 3. DEPLOY.md, README · 4. Bug bash → BUGS.md | v1 done (§1) | qa + devops |

---

## 4. Data track (parallel, ~2 days a week in W1–W6, then top-ups)

| By end of | Deliverable |
|---|---|
| W1 | 8 ward polygons + special zones (Yamuna floodplain/ghats incl. Kalindi Kunj, NDMC, Cantonment) in `data/*.geojson` |
| W2 | OSM roads for the 8 wards imported (`import_osm.py`) and the PWD notified road list typed into `pwd_list.csv` |
| W3 | `pwd_matches.csv`: PWD names matched to OSM segments by hand, with boundary roads included |
| W4 | `rules.csv`, `departments.csv`, `contacts.csv` (labelled "curated", each with a source + date) |
| W5 | 12–15 corpus docs in `corpus/manifest.yaml`, OCR text checked by eye |
| W5 | **Dev set of 50** cases (yours, used for tuning) |
| W6 | **Test set of 100**, labelled blind by the second labeller, then hashed into `eval/test.lock` (**frozen Fri 4 Dec**) |

Test-set coverage follows TECH-MEETING-01 §6. Never tune on the test set; `tune.py` only reads `dev/`.

---

## 5. Milestones

| # | Milestone | Date | Proof |
|---|---|---|---|
| M0 | Repo + CI green | Fri 30 Oct | badge |
| M1 | Thin slice end to end on fakes | Fri 13 Nov | Scenario A on fakes |
| M2 | Real AI + real routing | Fri 27 Nov | Scenario B on real models |
| M3 | Test set frozen | Fri 4 Dec | `eval/test.lock` committed |
| M4 | All 3 scenarios local | Fri 11 Dec | screen recording |
| M5 | **Phase 1 done + go/no-go** | Fri 18 Dec | hosted demo + first eval report |
| M6 | Phase 2 done | Fri 15 Jan | tracker, ladder, letter, nudges, votes |
| M7 | **v1 shipped** | Fri 29 Jan | final eval report + video + DEPLOY.md |

---

## 6. Things only you can do (with dates)

| What | Needed by | Why it blocks |
|---|---|---|
| Create the GitHub repo, turn on Actions, add secrets | Tue 27 Oct | CI (W1) |
| Get API keys: **Groq**, **Gemini**, **Supabase** project (URL, anon key, service key, JWT secret) | Wed 28 Oct | real intake (W4), auth (W3) |
| **Find the second labeller** (a friend or classmate who hasn't seen `rules.csv`) and brief them | Fri 6 Nov | blind labels (W1 fix) |
| Second labeller finishes the 100 blind department labels, plus 20 double-labelled cases for agreement % | Fri 27 Nov | the test set freezes 4 Dec |
| **Record real Hindi/Hinglish voice clips** from real speakers (not TTS, not you): ~30 for the STT benchmark, plus test-set voices | 30 clips by Fri 20 Nov, the rest by Fri 27 Nov | STT choice (W4), test set (W6) |
| Take the demo photos (ghat, pothole, streetlight) or pick openly licensed ones | Fri 4 Dec | demo scenarios |
| Book the demo slot and rehearse with `warmup.sh` | W14 | cold starts |

---

## 7. Top 5 risks and fallbacks

| # | Risk | Early sign | Fallback |
|---|---|---|---|
| 1 | Data curation slips (PWD → OSM matching is manual) | `pwd_matches.csv` not done by W3 | **Stay at 8 wards**; drop ward growth in W12 |
| 2 | Free-tier limits (Groq/Gemini rate caps, Render sleeps) | 429 errors during eval runs | JSON cache plus resumable eval batches; the demo runs from **cached replay** |
| 3 | Second labeller late, so the test set can't freeze | labels not done by 27 Nov | Freeze what exists on 4 Dec (at least 60 cases), report the smaller n and wider uncertainty honestly, and top up before W14 only if the new cases are kept fully separate |
| 4 | Streamlit on mobile is rough (geolocation, camera, 3G) | phone test in W7 fails | **Demo on desktop + phone video**; geolocation falls back to landmark text |
| 5 | Hindi corpus OCR is bad (scans, KrutiDev fonts) | span check fails on Hindi docs in W6 | Use the English versions of the same rules; keep at least 12 clean docs; "no cited rule found" is a valid answer |

---

## 8. Definition of done (any task)

A task is done only when all 4 are true:
1. **Works:** it runs in the week's demo check, not just in theory.
2. **Has 1 test:** at least one pytest that fails if the logic breaks (fakes, no real API calls).
3. **Is explainable:** you can explain what it does and why in 2 minutes, without notes.
4. **Interview line:** if anything was hard, add one line to `docs/INTERVIEW-PREP.md` (problem → what you tried → what worked).

---

## 9. Cut list (drop in this order if behind)

1. All of Phase 3 (C2 map first, then C1, C3, C4, C5)
2. S3 re-shoot + votes (keep "status unknown" as the default)
3. S7 passer-by confirm and the reminder email (keep the in-app cards)
4. S4 CPGRAMS + RTI drafts (keep the reminder + first appeal)
5. Ward growth beyond 8
6. S5 re-route (keep the evidence letter)
7. S1/S2 polish (keep the "unverified location" flag)
8. Hosted demo → local demo + phone video

**Never cut:** any Must (M1–M8), S6 Call mode, the 3 demo scenarios, and the eval on the frozen test set.

# Sprint 6 API contract: official citations + follow-up Q&A

**Status: DRAFT (rev 2), waiting for user approval.** Design, options and decisions: `docs/HLD-S6.md`.
Extends sprints 2–5 and deploy-api.md; everything there still holds. Routing is unchanged: plain SQL
plus the rules choose the department. This sprint attaches evidence and answers questions. Free tiers only.

## 1. Flow

```
 ┌─────────────── OFFLINE: GitHub Action ingest.yml (monthly + manual) ───────────────┐
 │ sources.yaml ─► fetch (ETag) ─► text hash changed? ─► extract (trafilatura / pypdf)│
 │ ─► chunk (section, page) ─► Gemini dense 768 + fastembed BM25 ─► Postgres + Qdrant │
 │ (point id = chunk.id) ─► you approve new citations (review_corpus.py)              │
 └──────────────────────────────────────┬──────────────────────────────────────────────┘
                                        ▼
 ┌────────── DOSSIER ──────────┐   ┌──────────────────── Q&A: POST /reports/{id}/ask ────────────────────┐
 │ routing (SQL) → rule_id     │   │ owner? has dossier or out_of_coverage? limits?                      │
 │ approved citation?          │   │ format → mask → injection rules → OFF-TOPIC GATE ──off──► fixed     │
 │  yes → quote still in chunk │   │                                      (0 LLM tokens)  reply          │
 │        → citations snapshot │   │ → layers (national + delhi | state)                  │
 │  no / fails → "No official  │   │ → Qdrant dense 20 + bm25 20 → RRF → MiniLM rerank → 6 chunks [Cn]   │
 │   document found", ×0.8     │   │ + report facts [Yn] → LiteLLM: Groq → Gemini → 503 "try later"      │
 └─────────────────────────────┘   │ → cite-or-drop · numbers · department · PII · safety                │
                                   │ → nothing left: "I don't know from the official documents"          │
                                   │ → outside Delhi: + "I don't know the local rule" + state portal     │
                                   │ → report_event (masked) → GET /conversation                         │
                                   └──────────────────────────────────────────────────────────────────────┘
```

## 2. Document corpus

### 2.1 Two layers (D1, D2)
| Layer | Applies when | Documents | Count |
|---|---|---|---|
| `national` | always | RTI Act 2005, RTI Rules 2012 (central), DARPG CPGRAMS guidelines 2024, National Highways Act 1956 + NHAI 1033 / MoRTH page | ~5 |
| `state` | pin in that state (not Delhi) | official portal page of each verified `state_directory.csv` row (29); time-bound / right-to-services acts with official text (~10 first) | ~39 |
| `delhi` | pin in Delhi | ~33 official rule sources from `rules_draft.csv`, up to 6 department pages, Delhi Time Bound Delivery of Services Act 2011 | ~40 |

Tiers: `1` the issuing body's own text · `2` government news (newsonair.gov.in, PIB) and Parliament answers ·
`3` private news (never cited; 10 rule URLs today). The 25 Delhi wards stay the deep demo area and the test
set; outside Delhi the app never states a local rule (full India routing = ROADMAP V2-A).

### 2.2 Source list: `data/corpus/sources.yaml` (git)
```yaml
- key: djb-grievance
  url: https://delhijalboard.delhi.gov.in/jalboard/grievance-redressal-mechanism
  title: Grievance Redressal Mechanism
  issuer: Delhi Jal Board
  doc_type: official_page   # act|rules|notification|gazette|charter|court_filing|official_page|parliament_answer|govt_news
  tier: 1
  layer: delhi              # national|state|delhi
  state_code: DL            # for layer=state: the state_directory.csv code
  issued_on: null           # null = "date not stated"
  lang: en
  department_codes: [DJB]
  categories: [SEWER_OVERFLOW, MANHOLE_OPEN, WATER_NO_SUPPLY, WATER_LEAK, WATER_CONTAMINATED]
  rules: [EM-03, RD-07, DR-04, SW-01, SW-02, SW-04, WT-01]
  recrawl_days: 30
```

### 2.3 Fetch, store, chunk, embed, version
- **Fetch** (GitHub Action only): registry URLs only, ETag/Last-Modified, 1 request/s per host, generic
  User-Agent, robots.txt, 20 MB / 300 pages cap. HTML → `trafilatura`; PDF → `pypdf` (`pdfplumber` for tables);
  a scanned page → OCR (Tesseract `hin+eng`) only if that document is needed, else `needs_ocr`.
- **Postgres (source of truth):**
  - `policy_doc` (existing, = a source): + `key` unique, `issuer`, `doc_type`, `tier`, `layer`, `state_code`,
    `issued_on`, `department_codes text[]`, `categories text[]`, `recrawl_days`, `status` (`active|dead|retired`),
    `etag`, `last_modified`, `last_checked_at`, `last_ok_at`, `fail_count`.
  - `doc_version` (new): `id`, `doc_id`, `version_no`, `sha256` (cleaned text), `fetched_at`, `raw_key`
    (private bucket), `valid_from`, `valid_to`, `ocr_pages`, `status` (`pending_review|approved|superseded|rejected`), `reviewed_at`.
  - `chunk` (existing): + `doc_version_id`, `ordinal` (`UNIQUE (doc_version_id, ordinal)`), `text_sha`,
    `injection_flag`, `status`. `text` kept for quoting; `tsv` kept for the fallback search.
  - `citation` (existing): + `rule_id`, `role` (`ownership|deadline`), `status` (`proposed|approved|needs_review|rejected`), `reviewed_at`.
- **Qdrant Cloud:** alias `rule_chunks` → collection `rule_chunks_v2`. Named vectors: `dense` (768, cosine,
  Gemini `gemini-embedding-001`, re-normalised) and sparse `bm25` (fastembed `Qdrant/bm25`, IDF modifier).
  **Point id = `chunk.id`.** Indexed payload: `status`, `tier`, `layer`, `state_code`, `department_codes`,
  `categories`, `lang`, `valid_to`, `doc_id`. No text in Qdrant.
- **Chunking:** clause/section headings, 200–450 tokens, one-sentence overlap, `section_ref` + `page` kept.
- **Versioning:** new cleaned-text hash = new `doc_version`; old versions are never deleted. Only changed chunks
  are re-embedded. Re-crawl every **30 days**; `stale` when not checked OK in **90 days**. Dead link (404/410 or
  3 failures) → last approved copy keeps serving with `link_status: "broken"`.

## 3. Dossier `citations` (GET /dossiers/{id})
Values are illustrative, not real quotes. `claims` stays `[]` for one sprint, then is removed.
```json
"citation_status": "cited",
"citations": [
  {"id": 812, "role": "ownership", "rule_id": "DR-02",
   "doc_title": "Affidavit by SDMC in OA No. 06 of 2012", "issuer": "South Delhi Municipal Corporation (before the NGT)",
   "doc_type": "court_filing", "tier": 1, "layer": "delhi", "doc_date": "2019-05-14",
   "section_ref": "Para 7", "page": 4, "quote": "…exact words from the stored chunk…",
   "url": "https://www.greentribunal.gov.in/…", "fetched_at": "2026-11-12", "doc_version": 1,
   "stale": false, "link_status": "ok"}
],
"deadline": {"sla_days": 15, "due_date": null, "label": "suggested", "citation": null}
```
- `citation_status`: `cited` | `none`. `none` → `citations: []`, UI shows `no_official_document`; the routing
  confidence already has ×0.8 (the engine now checks "an approved citation exists" instead of "`rule_source_url` not empty").
- `doc_date` null → "Date not stated"; `stale` → "Last checked {fetched_at}"; `link_status: broken` → "Link not working".
- `deadline.citation` is a citation with `role: "deadline"` when approved (today only CPGRAMS).
- Citations are a **snapshot** taken when the dossier is built; a quote is copied only if
  `normalise(quote)` is in `normalise(chunk.text)` at that moment. No model text in a dossier citation.

## 4. POST /api/v1/reports/{id}/ask

Allowed when the report has a dossier **or** ended as `out_of_coverage` (any place in India).
Request:
```json
{"question": "PWD kyun, MCD kyun nahi?", "client_msg_id": "5b1d…-uuid"}
```
- `question`: 1–300 characters after trimming, plain text. Masked (`mask_pii`) before it is stored, traced,
  cached, embedded or sent to a model.
- `client_msg_id`: UUID from the UI; the same id again returns the stored answer (no new calls, not counted twice).

Response `200` (illustrative):
```json
{
  "message_id": 4411,
  "status": "answered",
  "layer": "delhi",
  "sentences": [
    {"text": "Your report went to PWD because the road is on PWD's list of roads it maintains.", "cites": ["Y3", "C1"]}
  ],
  "sources": [
    {"id": "Y3", "kind": "your_report", "label": "Routing reason", "quote": "Road is on the PWD list (Mathura Road), so PWD maintains it"},
    {"id": "C1", "kind": "document", "doc_title": "Roads with PWD", "issuer": "PWD, GNCTD", "doc_type": "official_page",
     "tier": 1, "layer": "delhi", "doc_date": null, "section_ref": "Roads list", "page": null, "quote": "Mathura Road",
     "url": "https://www.pwddelhi.gov.in/maintenance-inventory/roads-with-pwd", "fetched_at": "2026-11-12",
     "stale": false, "link_status": "ok"}
  ],
  "local_rule_unknown": false,
  "where_to_ask": [],
  "disclaimer_key": "qa_disclaimer",
  "provider": "groq", "cached": false, "degraded": false
}
```
`status` values (each is a normal `200`):
| `status` | When | Text shown | LLM tokens |
|---|---|---|---|
| `answered` | ≥ 1 sentence passed every output check | the sentences | yes |
| `not_found` | the model said not_found, or every sentence was dropped, or the safety check failed | `qa_not_found` + `where_to_ask` | yes |
| `out_of_scope` | about other users/reports, changing the department, legal advice beyond the text | `qa_out_of_scope` + `where_to_ask` | yes |
| `off_topic` | the off-topic gate (keywords / similarity / small classifier) | `qa_off_topic`: "I don't know — this is outside civic complaints." | **none** (classifier only when borderline) |
| `blocked` | injection rules | `qa_blocked` | none |

- `layer`: `delhi` | `state` | `national`. For a pin outside Delhi, `local_rule_unknown: true` and the text
  ends with `qa_local_rule_unknown` ("I don't know the local rule for {state}.") + the state portal.
- `where_to_ask`: `[{label, value, deep_link}]`: the dossier's department channels; outside Delhi the state
  portal/helpline from `state_directory.csv` (verified rows); then PGMS (Delhi) / CPGRAMS; RTI Online for RTI questions.
- `sources` lists only cited ids. URLs, phone numbers and department names come from the DB.
- `provider`: `groq` | `gemini` | `none`. `degraded: true` when keyword-only retrieval or no reranker was used.

Errors (same envelope as before):
| When | Status | `message_key` (`code`) |
|---|---|---|
| Not logged in | 401 | `login_required` |
| Not your report / no such report | 404 | `report_not_found` (`not_found`) |
| Report still in intake or rejected as junk | 409 | `qa_needs_dossier` (`wrong_step`) |
| Empty, > 300 chars, only URLs/emoji, bad `client_msg_id` | 422 | `invalid_question` (`validation`) |
| > 10 per report or > 40 per user in 24 h | 429 | `rate_limited_questions` (`rate_limited`) |
| Site-wide free-quota guard (≈ 80 % used) | 429 | `qa_busy` (`rate_limited`) |
| Groq and Gemini both failed / both breakers open | 503 | `qa_unavailable` (`provider_down`) |

Limits are counted from `model_call` events in SQL under the per-user advisory lock (the SEC-015 pattern),
**before** any model or embedding call. Every question counts, including off-topic and blocked ones.
Config: `RATE_QUESTIONS_REPORT=10`, `RATE_QUESTIONS_DAY=40`, `QA_QUOTA_GUARD=0.8`, `RERANK=on`.

### POST /api/v1/reports/{id}/ask/{message_id}/feedback
`{"vote": "up" | "down", "reason": "wrong | not_helpful | other"?}` → `204`. Owner-only; the last vote wins.
Stored as a `report_event` (`kind: ask_feedback`) and a Langfuse score.

## 5. Q&A in GET /reports/{id}/conversation
Two new `kind`s, same message shape as Sprint 5:
```json
{"role": "user", "kind": "ask", "text": "PWD kyun, MCD kyun nahi?", "at": "…"}
{"role": "assistant", "kind": "ask_answer", "message_id": 4411, "status": "answered", "layer": "delhi",
 "text": "Your report went to PWD because … [Y3][C1]", "sentences": [...], "sources": [...],
 "local_rule_unknown": false, "vote": null, "options": [{"value": "…", "label": "…", "deep_link": "…"}], "at": "…"}
```
`text` = the joined sentences, or the fixed reply. `options` = `where_to_ask`. User text is stored masked.
`model_call` events stay hidden, as in Sprint 5.

## 6. Grounding and refusal rules
1. Sources are only `[Y1..Yn]` (this report's facts, written by code) and `[C1..C6]` (approved chunks from the
   layers that apply to the pin). Delhi chunks are filtered out for a pin outside Delhi.
2. Every sentence needs ≥ 1 cite with an exact quote (4–40 words, or an exact name/number on list pages) that is a
   substring of its source after `normalise()`. Failing sentences are dropped, never fixed.
3. Every number in a sentence must be in one of its verified quotes.
4. A sentence naming a department other than the report's department or its candidate cards (outside a quote) is dropped.
5. A sentence containing PII (`mask_pii` changes it) is dropped. The safety check failing replaces the whole answer.
6. Nothing left → `not_found`. Off-topic → `off_topic` before any LLM call.
7. Memory: the last 2 Q&A turns of this report (masked question + verified answer text). Nothing else.
8. Suggested deadlines are described as suggested, because `[Yn]` says "no official deadline was found".
9. Q&A never changes the department, the rule or the deadline. There is no write path from an answer.

## 7. Prompt injection and privacy
- Document text is **data**: in `<doc …>` blocks with fake tags stripped; injection-like chunks are flagged at placed in `<question>`.
- The model returns schema-checked JSON only, has no tools, and its text is rendered as plain text.
- The model never sees other users' data; `rule_chunks` holds public documents only.
- Langfuse, the cache, Gemini embeddings and `report_event` get masked text only.

## 8. New message keys
`no_official_document` ("No official document found") · `citation_stale` ("Last checked {date}") ·
`citation_link_broken` ("Link not working since {date}") · `citation_date_unknown` ("Date not stated") ·
`citation_tier_govt_news` ("Government news") · `citation_tier_parliament` ("Parliament answer") ·
`qa_placeholder` ("Ask about your report…") · `qa_checking` ("Searching official documents… checking quotes…") ·
`qa_not_found` ("I don't know from the official documents. You can ask here:") ·
`qa_out_of_scope` ("I can only answer about your report, from official documents.") ·
`qa_off_topic` ("I don't know — this is outside civic complaints.") ·
`qa_blocked` ("I can't help with that. Ask me about your complaint.") ·
`qa_local_rule_unknown` ("I don't know the local rule for {state}. You can ask here:") ·
`qa_disclaimer` ("Not legal advice. Based on official documents as last checked.") ·
`qa_needs_dossier` (409) · `invalid_question` (422) · `rate_limited_questions` (429) · `qa_busy` (429) ·
`qa_unavailable` (503) · `qa_feedback_thanks`

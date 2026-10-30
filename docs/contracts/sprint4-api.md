# Sprint 4 API contract: real AI intake (LangGraph), emergency path, state directory

Extends `sprint2-api.md` and `sprint3-api.md`; everything there still holds. The design is in ARCHITECTURE §3 (intake graph), simplified per §0.

## What changes
The fake intake (`providers/fakes.py`, fixed "POTHOLE") is replaced by a **LangGraph intake graph**:

```
 photo ──► assess_image (Gemini Flash) ─┐
                                        ├─► fuse (plain code) ─┬─ junk          → rejected_junk
 text  ──► assess_text  (Groq LLM)  ────┘                      ├─ emergency     → emergency card + continue
                                                               ├─ conf < 0.7    → PAUSE: clarify_category
                                                               ├─ photo ≠ text  → PAUSE: clarify_category
                                                               └─ ok            → route (SQL, unchanged)
```

- **The department is still decided only by SQL.** The AI returns category, confidence, junk, emergency, `drain_type`, `road_judgement` and face/plate boxes. It never returns a department.
- **Severity is not the model's choice (B-035).** It is the category's `default_severity` from `data/categories.csv`, raised to `critical` only by the emergency flag (Sprint 5 adds the night-safety rule). The text model may still return a `severity` field; it is ignored, so "Severity critical!!" in the text changes nothing.
- **Model names come only from `.env`:** `VISION_MODEL` (default `gemini-3.5-flash-lite`) and `TEXT_MODEL` (default `openai/gpt-oss-120b`).
- **User text is data, never instructions.** Prompts put it inside a delimited block, and the system prompt says to ignore any instructions it contains (prompt injection).
- **Strict JSON.** Every model output is validated by a Pydantic schema. Invalid output → retry once (skipped once 25 s have passed) → otherwise treated as "unsure" (confidence 0). The model's `landmark` is capped at 200 characters.
- **Time budget (B-031):** each model request times out after 20 s (`LLM_TIMEOUT_S`), at most 2 requests per model. A retry starts only if it can still finish within 50 s of the first try; a 429's `Retry-After` is honoured when it fits, otherwise the provider counts as down. The photo and text calls run in parallel, so a report answers well inside the UI's 90 s request timeout.
- **One provider down:** if one model is down or rate-limited and the other answered, the graph decides from the one that answered (and asks `clarify_category` if that one is unsure). Only when every model the report needed is down → 503 `ai_unavailable`.
- **Cache:** identical inputs (a hash of photo bytes + text + model + the **full system-prompt text**) reuse the stored answer (`.cache/llm/`, git-ignored). Editing a prompt without bumping its version can't reuse old answers.
- **Logging (B-038):** every provider failure writes one server-log line (step, model, error type, latency, first 80 characters of the masked input), with or without Langfuse.
- **Tracing:** every model call is traced to **Langfuse** when `LANGFUSE_*` keys exist, with masked text only and no photo bytes. When there are no keys, tracing is off and nothing breaks.
- **Tests never call real models:** `PROVIDER_MODE=fake` (the default in tests) uses scripted fakes. `live` is the default for the app.

## POST /api/v1/reports: response changes
New possible `state` values, which reuse the ReportStep envelope from ARCHITECTURE §7:

| state | `next` | Meaning |
|---|---|---|
| `dossier_ready` | `null` | as before |
| `needs_clarification` | `{"kind": "clarify_category", "options": [{"category": "POTHOLE", "confidence": 0.55}, …], "allow_other": true, "mismatch": null \| {"image": "GARBAGE_DUMP", "text": "streetlight"}}` | the graph is paused, waiting for the user |
| `rejected_junk` | `{"kind": "retake_photo", "reason": "selfie \| meme \| screenshot \| blank \| unrelated"}` | photo isn't a civic problem (and there's no usable text) |
| `out_of_coverage` | as in S3 (`next.kind = "general_channels"`) | `state_directory` is a **top-level** field of the response, next to `next`, not inside it (below) |
| (503 error) `ai_unavailable` | — | every needed model was down; the report is kept with `state = 'ai_unavailable'` and does **not** count toward the daily limit (B-033) |

**Emergency:** when `emergency: true` (live wire, gas leak, open manhole, fallen tree on the road or a vehicle, building collapse, fire), the response adds:
```json
"emergency": {"kind": "live_wire", "call_first": [{"label": "Emergency 112", "value": "112", "deep_link": "tel:112"},
                                                {"label": "BSES Rajdhani", "value": "19123", "deep_link": "tel:19123"}]}
```
The report still continues to a dossier. Emergency only adds the "call first" card; it never blocks.
For a live wire whose pin is in no `discom_area` zone (e.g. outside the coverage wards), the card lists 112 plus all three discoms, labelled by area: BSES Rajdhani (South & West Delhi) 19123, BSES Yamuna (East & Central Delhi) 19122, Tata Power-DDL (North & North-West Delhi) 19124 (B-036).
An emergency dossier has no N-day deadline: `deadline = {"sla_days": null, "label": "immediate"}`, and the letter says "Immediate action requested (safety risk)." instead of "within 15 days" (B-037).

**Notices (new):** `photo_not_checked`: the vision model did not look at the photo, either because it was down or because the photo was not sent (below). The text decided, or the user is asked. A photo the model was down for is still blurred with OpenCV and kept.

**Photo sent to the model (SEC-013):** Gemini only receives a copy already blurred by OpenCV (faces/plates). The shared copy is blurred again on the original with OpenCV ∪ the model's boxes. If the OpenCV pre-blur fails, the photo is **not sent** to the model at all. That is a privacy choice, not an outage:
- photo + usable text: the text decides (`photo_not_checked`);
- **photo only:** `201 needs_clarification` with `next = {"kind": "clarify_category", "options": [], "allow_other": true, "mismatch": null}`; the user picks the category, and the dossier has `photo_withheld: true` (`blur_status = 'failed'`).
503 `ai_unavailable` is only for real provider outages.

**Photo (B-032):** a vision-model failure never withholds the photo. `photo_withheld: true` means only that blurring itself failed. A junk photo that was dropped because the text was usable has `photo_url: null` and `photo_withheld: false` (`media.blur_status = 'junk'`).

## POST /api/v1/reports/{report_id}/clarify
Body JSON: `{"category": "POTHOLE"}` (must be one of the offered options, or any valid category code if `allow_other`).
Response: the same envelope as POST /reports (usually `dossier_ready`). It resumes the paused graph (LangGraph checkpointer in Postgres, `thread_id = report_id`). Errors: 409 `wrong_step` (not paused, or another answer got there first), 422 `invalid_category`.
The resume is atomic (TL#2): `UPDATE report SET state = 'resuming' WHERE id = … AND user_id = … AND state = 'needs_clarification' RETURNING 1`. Of two parallel answers only one gets the row; the other gets 409.

**Note (TL#6):** the same-spot 429 (`rate_limited_same_spot`) needs the category, so it can only arrive **after** the model calls, or after `/clarify`. The daily and cooldown limits are checked before any model call. The same-spot check never counts the report itself.

## Category codes and GET /api/v1/categories
**`data/categories.csv` is the only source of category codes**: its `code` column, 37 UPPERCASE codes (`POTHOLE`, `GARBAGE_DUMP`, `WATERLOGGING`, `STREETLIGHT_OUT`, `LIVE_WIRE`, … `OTHER`), used **verbatim** everywhere: model output, `report.category` / `master_issue.category`, clarify `options` / `mismatch`, the `/clarify` body and `GET /categories`. Never lower-cased; there are no other spellings (no `garbage`, no `waterlogging_sewage`). A code the model invents (or a wrong-case one) counts as "unsure" (confidence 0), which leads to `clarify_category`. Old local dev rows may still hold lower-case values; they are not migrated.

### GET /api/v1/categories
Login needed. The UI's "Something else" picker uses it; any of these codes is accepted by `/clarify` when `allow_other` is true. CSV order.
```json
{"categories": [
  {"code": "POTHOLE", "name_en": "Pothole", "group": "roads", "emergency": false},
  {"code": "ROAD_CAVE_IN", "name_en": "Road cave-in", "group": "roads", "emergency": true},
  …
  {"code": "OTHER", "name_en": "Other / not sure", "group": "other", "emergency": false}
]}
```

## State directory (outside coverage, Layer 1)
`data/state_directory.csv`: `state_code, state_name, portal_name, portal_url, helpline, source_url, accessed, verified`, covering all 36 states/UTs. A pin outside Delhi → which state (state boundaries) → return:
```json
"state_directory": {"state": "Haryana", "portal_name": "CM Window", "portal_url": "https://…", "helpline": "…",
                    "verified": true, "national": {"name": "CPGRAMS", "url": "https://pgportal.gov.in"}}
```
If the state can't be found or isn't verified, show CPGRAMS only.
**Where it is (B-034):** `state_directory` is at the **top level** of the POST /reports (and /clarify) response, e.g. `{"state": "out_of_coverage", "next": {"kind": "general_channels", …}, "state_directory": {…}, …}`. Clients must read `response["state_directory"]`, never `response["next"]["state_directory"]`.

## New error / message keys (the UI needs English text for each)
`invalid_category` (422) · `wrong_step` (409) · `ai_unavailable` (503: every needed provider down after retries; kept as `state='ai_unavailable'`, not counted in the daily limit) · `photo_not_checked` (notice) · `retake_selfie`, `retake_meme`, `retake_screenshot`, `retake_blank`, `retake_unrelated` (the `rejected_junk` reasons) · `emergency_call_first` · `state_portal`

## State boundaries table (backend migration 0004)
`state_boundary(code text primary key, name text, geom geometry(MultiPolygon,4326))` with a GIST index, loaded by `scripts/load_states.py` from `data/states.geojson`.

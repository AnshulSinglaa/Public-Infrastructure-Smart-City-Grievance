# Bugs

QA run: Sprint 2 thin slice, 27 Oct 2026. API on :8010, UI on :8501 (API_URL=http://localhost:8010).

**Summary:** ruff clean · pytest 7/7 (backend) + 3/3 (ui, run by hand). 40 API cases + 40 concurrent posts all behave as expected except those below. W2 demo check passes: photo + text → fake answer → dossier (dept, ward 185, hi/en complaint, call `tel:155305`, demo notice). No horizontal scroll at 375 px.
Not tested: camera capture (blocked in the browser pane), real `tel:` dialing, network throttling, screen reader.

| ID | Sev | Area | Title | Status |
|---|---|---|---|---|
| B-001 | major | frontend + contract | API error keys have no UI text, so the raw key shows | Verified |
| B-002 | major | frontend | Sidebar can't be reopened once collapsed (no language switch on phones) | Verified |
| B-003 | major | frontend | Material icons render as words ("keyboard_double_arrow_left", "upload", "add") | Verified |
| B-004 | major | devops/config | Every UI→API call takes ~2.2 s with `localhost` | Verified |
| B-005 | major | backend | Pin outside every ward silently becomes ward 185, "very likely" | Verified |
| B-006 | minor | backend | Two `photo` parts accepted (contract: max 1) | Verified |
| B-007 | minor | tests/CI | `uv run pytest -q` does not run `ui/tests` | Verified |
| B-008 | minor | frontend | Uploader says "200MB per file", limit is 5 MB | Verified |
| B-009 | minor | backend | Unknown routes return `{"detail": ...}`, not the `{"error": ...}` shape | Verified |
| B-010 | minor | frontend | Complaint tabs open on हिंदी even when the UI is English | Verified |
| B-011 | major | frontend | Switching language after choosing a gallery photo silently drops the photo | Verified |
| B-011 | major | frontend | Switching language clears the uploaded gallery photo (found in re-verify) | Fixed |

---

### B-001 · major · frontend + contract — raw error key shown
**Status:** Fixed. Cause: `ui/i18n.py` had text for only one API `message_key`, and `t()` falls back to the key. Added hi/en for every key, `_check` maps unknown keys to `server_error`, contract lists all keys (test reads keys from backend source). **QA re-verify 27 Oct: Verified.**
Steps: UI → गैलरी से → upload a text file named `fake.jpg` → Prepare complaint.
Expected: friendly hi/en message.
Actual: red box reads `photo_not_jpeg_png`. Same for `photo_too_large`, `text_too_long`, `need_lat_and_lng`, `invalid_input` (API returns them; `ui/i18n.py` has none of them, `t()` falls back to the key). The contract only lists `need_photo_or_text`, and doesn't mention the `detail.fields` extra or the 404 `not_found` shapes.
Screenshot: `C:\Users\singl\.claude\projects\C--Users-singl-Downloads-Public-Infrastructure---Smart-City-Grievance\357b2e09-ecae-4e50-a983-62470123d4d5\tool-results\mcp-Claude_Browser-blob-1793048186078-h58ijg.jpg`

### B-002 · major · frontend — sidebar can't be reopened
**Status:** Fixed. Cause: `style.css` hid `stToolbar`, which holds `stExpandSidebarButton` in Streamlit 1.66. Now only `stToolbarActions` is hidden. Checked at 375 / 800 / 1280 px, collapse + reopen. **QA re-verify 27 Oct: Verified.**
Steps: open the UI at 375 px (the sidebar starts collapsed) or collapse it on desktop.
Expected: an arrow to reopen it (language, nav, text size live there).
Actual: no arrow. `ui/style.css` hides `[data-testid="stToolbar"]`, and in this Streamlit version `stExpandSidebarButton` sits inside it (width 0). Phone users cannot switch to English or reach "My issues".

### B-003 · major · frontend — icon names shown as text
**Status:** Fixed. Cause: the `[class*="st-"]` font rule overrode the Material Symbols icon font. Font now set natively via `[theme] font` in `ui/.streamlit/config.toml`; the CSS rule is gone. **QA re-verify 27 Oct: Verified.**
Steps: open the UI on desktop; pick गैलरी से; upload a file.
Expected: arrow / upload / add icons.
Actual: text "keyboard_double_arrow_left" at top-left of the sidebar, "uploadUpload" button, "add" next to the file chip. Cause: `style.css` rule `[class*="st-"] { font-family: Inter... }` also hits `stIconMaterial` spans (computed font = Inter).

### B-004 · major · devops/config — 2.2 s per API call
**Status:** Fixed. Cause: default `API_URL` used `localhost`, Windows tries `::1` first and uvicorn is IPv4-only. Default is now `http://127.0.0.1:8010` (app, .env.example, README, contract); health cache TTL 30 s. **QA re-verify 27 Oct: Verified.**
Steps: `httpx.get("http://localhost:8010/healthz")` vs `http://127.0.0.1:8010`.
Expected: ~10 ms.
Actual: 2.19 s vs 0.01 s. uvicorn listens on 127.0.0.1 only; Windows tries `::1` first and waits. Submit = 2 calls ≈ 4.5 s spinner, and the health check (TTL 10 s) blocks reruns: switching language shows "Running api_health()" for ~3 s. Fix in default `API_URL` / README (127.0.0.1) or uvicorn `--host`.

### B-005 · major · backend — wrong ward for pins outside Delhi
**Status:** Fixed. Cause: `route()` used `DEFAULT_WARD` for any pin with no ward match. Pins in no ward / an uncovered ward now return `state: out_of_coverage` + `general_channels`, no claim; ward 185 only when there is no pin. UI shows a "not covered yet" helpline card. **QA re-verify 27 Oct: Verified.**
Steps: `POST /api/v1/reports` text=x, lat=19.07, lng=72.87 (Mumbai). Same with lat=0, lng=0.
Expected: 422 (e.g. `outside_service_area`), or no ward + low confidence.
Actual: 201; dossier says ward 185 Madanpur Khadar East, `confidence_label: very_likely`, `curated: true`. `routing/engine.py` falls back to `DEFAULT_WARD` for a real pin that matches no ward. (No pin → ward 185 is the agreed S2 fake and is fine.)

### B-006 · minor · backend — more than one photo accepted
**Status:** Fixed. Cause: `photo` was a single `UploadFile`, so FastAPI kept one part. Now a list; more than one → 422 `too_many_photos`. **QA re-verify 27 Oct: Verified.**
Steps: multipart with two `photo` parts.
Expected: 422 (contract "max 1 file").
Actual: 201, second file silently dropped.

### B-007 · minor · tests/CI — UI tests skipped
**Status:** Fixed. Cause: `testpaths` listed only `backend/tests`. Now both, with `ui` on `pythonpath`; `sys.path` hack removed. **QA re-verify 27 Oct: Verified.**
`pyproject.toml` `testpaths = ["backend/tests"]`, so `uv run pytest -q` runs 7 tests; `ui/tests/test_report.py` (3 tests, pass) only runs when named.

### B-008 · minor · frontend — wrong upload limit hint
**Status:** Fixed. Cause: no `server.maxUploadSize` set (Streamlit default 200). Set to 5 in `ui/.streamlit/config.toml`. **QA re-verify 27 Oct: Verified.**
Gallery uploader shows "200MB per file • JPG, PNG". Contract limit is 5 MB. Set `server.maxUploadSize` in `.streamlit/config.toml` (Streamlit only takes whole MB, so 5).

### B-009 · minor · backend — inconsistent 404 shape
**Status:** Fixed. Cause: no handler for Starlette's own 404. Added one returning `{"error":{"code":"not_found","message_key":"not_found"}}`. **QA re-verify 27 Oct: Verified.**
`GET /api/v1/media/../../etc` (any unknown route) → `404 {"detail":"Not Found"}`. Our other errors use `{"error": {"code", "message_key"}}`.

### B-010 · minor · frontend — complaint tab ignores UI language
**Status:** Fixed. Cause: tabs were hard-coded हिंदी first. Tab order now follows the UI language. **QA re-verify 27 Oct: Verified.**
Switch the UI to English and open a dossier: the complaint card opens on the हिंदी tab. Expected: English tab first when `lang == "en"`. (Also: the Hindi complaint uses the English ward name, as there is no `name_hi` for wards yet.)

### B-011 · major · frontend — language switch drops the photo (new, found in re-verify)
**Status:** Fixed. Cause: widgets without a `key` are identified by their label, so a translated label made a new, empty uploader. `file_uploader`, `camera_input` and the text-size `selectbox` now have fixed keys (text area already had one). AppTest: upload → switch language → submit keeps the photo. **Re-verified 28 Oct by `test_language_switch_keeps_gallery_photo` (passes): Verified.**
Steps: UI (English) → From gallery → upload `p.jpg` → switch sidebar to हिंदी (or the reverse) → Prepare complaint.
Expected: the photo stays selected and is sent.
Actual: the file chip disappears after the switch; if text is typed, the report goes through text-only (DB: latest `report.media_id` NULL, dossier has no "Your photo" card). Likely cause: `st.file_uploader` in `ui/report.py` has no `key` and its label is `t("from_gallery", lang)`, so a new language = a new widget. The text area keeps its value because it has `key="report_text"`.

### Re-verify, 27 Oct 2026 (same methods as the first run)
- B-001: fake.jpg in UI → "यह फ़ोटो JPG या PNG नहीं है…"; all API `message_key`s exist in hi + en.
- B-002: browser pane at 375 px and desktop: sidebar collapse → » button visible (30×30 px) → reopen → language switch works. No horizontal scroll at 375 px.
- B-003: icon spans use "Material Symbols Rounded"; «, », Upload and + render as icons; body text is still Inter.
- B-004: UI `API_URL` = 127.0.0.1:8010; `health()` 0.58 s (was 2.2 s); submit about 2 s.
- B-005: pins in Mumbai, (0,0) and ward 12 (not in coverage) → 201 `out_of_coverage`, no dossier, helpline in `next`. (UI doesn't send pins yet, so this UI path wasn't clicked.)
- B-006: two photo parts → 422 `too_many_photos`. B-007: `pytest -q` → 19 passed (backend + ui). Ruff clean.
- B-008: uploader shows "5MB per file". B-009: unknown route → `{"error":{"code":"not_found","message_key":"not_found"}}`. B-010: English UI opens the English tab.
- Regression: UI happy path with no language switch (gallery photo + Hindi text → dossier: MCD, ward 185, hi/en complaint, "Your photo" card, `tel:155305`, demo notice) OK. With a language switch mid-form, the photo is lost (B-011). All 40 API cases and 40 concurrent posts OK.

---

Passed (API): photo+text+pin in ward 185 → ward 185 · pin in ward 12 → ward 12 · text only · photo only (media served, bytes match) · empty / whitespace → `need_photo_or_text` · lat-only / lng-only → `need_lat_and_lng` · lat 91 / nan / inf / "abc" → 422 · 8 MB → `photo_too_large` · exactly 5 MB → 201 · .txt as .jpg, GIF → `photo_not_jpeg_png` · Hindi + emoji stored intact · 1000 chars ok, 1001 → `text_too_long` · unknown dossier → 404 `dossier_not_found` · bad uuid → 422 · SQL-injection and `$ward_no` template text stored verbatim, DB intact · `<script>` escaped in UI · 40 concurrent posts all 201.

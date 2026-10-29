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
| B-012 | major | frontend | A page refresh or dropped websocket signs the user out and loses the draft; refreshes also use up the per-IP login limit | Verified |
| B-013 | major | backend | The server accepts a client-sent `location_verified=true` for any uploaded photo | Verified |
| B-014 | major | backend | A pin exactly on a ward edge or vertex becomes `out_of_coverage` (`ST_Contains`) | Verified |
| B-015 | minor | backend | Parallel requests get past the rate limits (5 same-spot posts → 4 reports) | Verified |
| B-016 | minor | frontend | At 375 px with 150 % text, Remove is pushed past the card edge and the sidebar is cramped; location chip is 34 px tall | Verified |
| B-017 | minor | backend | Devanagari digits in the user's text are rewritten to ASCII in `citizen_text` and the letter | Verified |
| B-018 | minor | frontend + backend | A pin outside Delhi (Noida) gets the generic card that says the helplines cover "anywhere in Delhi" | Verified |
| B-019 | minor | backend | A Hinglish landmark (the placeholder example) finds nothing, and there is no fallback | Verified |
| B-020 | minor | backend | Zero-width-only text counts as text and makes a dossier | Verified |
| B-021 | minor | frontend | A map tap after picking a landmark moves the pin silently (no confirm or "moved X m") | Verified |
| B-022 | minor | backend + contract | Names and house numbers in the text are not masked | Verified |
| B-023 | minor | backend | `/media` responses have no `X-Content-Type-Options: nosniff` | Verified |
| B-024 | minor | backend | `/docs` and `/openapi.json` are open without a token | Verified |
| B-025 | minor | devops/config | Streamlit listens on all interfaces (`::`), not 127.0.0.1 | Verified |
| B-026 | minor | frontend | My reports shows UTC dates (filed 29 Oct IST, shown as 2026-10-28) | Verified |
| B-027 | minor | backend | Hindi house numbers (`मकान नं. 45`) are not masked (found in re-verify) | Fixed |
| B-028 | minor | frontend | A refresh keeps the token and the draft text but drops the attached photo (found in re-verify) | Fixed |

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

---

## Sprint 3 run, 29 Oct 2026 (real API on :8010, UI on :8501, DB :5433)

**Summary:** ruff clean · pytest 97/97. Full flow works on the real API: login → composer (Hinglish text + upload) → landmark or map click → dossier (blurred photo, masked text, English letter, Call, demo notice) → My reports → Recent. Wards 185, 145, 30, 144, 57, 82 and 180 route correctly. A non-coverage ward (12, 146), Noida and Mumbai give `out_of_coverage`. EXIF/GPS is stripped (served copy = `_blur` file). Another user gets 404 on someone else's dossier and media. Phones are masked in all three forms. 11th login → `rate_limited_login`, same spot → `rate_limited_same_spot`, 11th report → `rate_limited_daily`, each with English text in the UI. Every protected endpoint returns 401 `login_required` without a valid token (including `/geocode`, forged or expired tokens and non-ASCII tokens). No stack traces. Docker ports are on 127.0.0.1. `.env` and `.cache/` are git-ignored. `showErrorDetails = false`. The UI has no Hindi strings (only `fake_api.py` test data).
Not tested: real face/plate blur (no face photo available), the GPS paths (camera and geolocation are blocked in the browser pane), DB down, slow network.
Test data: users "QA Alpha…Zeta", "QA Limit n", "QA Browser" (`@example.com`). **The login limit for 127.0.0.1 was used up at ~21:2x UTC.** It clears after 10 min, or when the API restarts.

### B-012 · major · frontend: refresh signs you out and loses the draft (FLOW-02, INF-07)
**Status:** Fixed. Cause: the token lived only in `st.session_state`, which a refresh wipes. Token + draft text are now kept in `localStorage` (streamlit_js_eval), restored on load and checked once with new `GET /me` (not rate-limited); Sign out wipes them. Checked in the browser: F5 keeps the login and the draft. Photo drafts are not kept (too big for localStorage). Awaiting QA re-verify.
Steps: sign in, type text and attach a photo, press F5 (or let Android drop the websocket while the camera app is open).
Expected: still signed in; the draft (or at least the session) survives.
Actual: back to "Sign in to continue", draft gone. The token lives only in `st.session_state`. Every refresh means a new `dev-login`, and the limit is 10 per IP per 10 min, so a few refreshes lock out every user behind the same IP/NAT.

### B-013 · major · backend: client decides `location_verified` (LOC-10)
**Status:** Fixed. Cause: the server stored the client's `location_verified` as fact. It stays a claim: every photo now gets `location_reported_by_phone` ("Location reported by phone (not verified)") or `photo_unverified_location`, a test shows it never changes routing, and the contract lists it as an accepted limitation. Awaiting QA re-verify.
Steps: `POST /reports` with an uploaded photo and `location_verified=true`.
Expected: the server never trusts the flag; a gallery upload stays unverified.
Actual: 201. `report.location_verified = true` in the DB, and no `photo_unverified_location` notice. Any script can mark its photos "verified".

### B-014 · major · backend: pin on a ward edge = out of coverage (LOC-05 ★)
**Status:** Fixed. Cause: `ST_Contains` is false on the boundary. `find_ward` now uses `ST_Covers`, falling back to the nearest covered ward within 15 m (`ST_DWithin` on `::geography`). Tested with the 185/186 corner, an edge midpoint and points 10 m / 40 m outside. Awaiting QA re-verify.
Steps: pin at the shared 185/186 vertex `28.5112908, 77.3124494` (computed with PostGIS from the two polygons).
Expected: both wards as candidates, or at least one of them.
Actual: 201 `out_of_coverage`. `ST_Contains` is false for every ward (`ST_Covers` is true for 185 and 186). `routing/engine.py` `find_ward`.

### B-015 · minor · backend: rate limits race (FLOW-04, DUP-12)
**Status:** Fixed. Cause: the limit counts and the insert weren't serialised. `pg_advisory_xact_lock(hashtext(user_id))` now runs before the checks; 5 parallel posts → 1 × 201 + 4 × 429 (test fails without the lock). Awaiting QA re-verify.
Steps: 5 parallel `POST /reports` from one user at the same pin.
Expected: 1 × 201, 4 × 429 `rate_limited_same_spot`.
Actual: `[429, 201, 201, 201, 201]`: 4 reports at one spot. A double-tap or a bot gets past the per-spot (and daily) limit. Known `ponytail` comment in `create_report`.

### B-016 · minor · frontend: 375 px + 150 % text layout (FLOW-21)
**Status:** Fixed. Cause: `flex-wrap: nowrap` on the photo row and toolbar. Both now wrap, the chip is capped at 16rem (ellipsis) and is 44 px tall, and the sidebar label/status got spacing/nowrap. Checked at 375 px + 150 %: everything stays inside the card. Awaiting QA re-verify.
Steps: 375 px, Settings → Text size 150 %, attach a photo.
Expected: everything inside the composer card.
Actual: the Remove button's right edge is at 464 px (card ends at 351 px, viewport 375). Only the "×" shows; there is no page scroll because the overflow is clipped. The sidebar label "RECENT" crowds the first item, and "connected" wraps under the name. At 100 % it is fine. The location chip is 34 px tall (UX: buttons ≥ 44 px). The max size is 150 % (catalog FLOW-21 asks 200 %).

### B-017 · minor · backend: user's Devanagari digits changed
**Status:** Fixed. Cause: `mask_pii` translated the whole text to ASCII digits. Phones are now found in an ASCII copy of the same length and masked in the original, so the user's characters stay as written. Awaiting QA re-verify.
Steps: text `वार्ड १८५ में do gaddhe hain`.
Expected: shown exactly as written (UX §14), with only phone numbers masked.
Actual: `citizen_text` and the letter say `वार्ड 185 में …`. `mask_pii` runs `translate(DEVANAGARI_DIGITS)` on the whole text, not just on the phone matches.

### B-018 · minor · frontend + backend: outside Delhi looks like "not covered yet" (LOC-07 ★)
**Status:** Fixed. Cause: `out_of_coverage` didn't know about the state border. A Delhi outline (`routing/delhi_outline.geojson`, OpenCity 2022 wards + NDMC + Cantonment) sets `next.outside_delhi`. Outside Delhi there are no channels, and the UI shows "This is outside Delhi … CPGRAMS" with a portal link. Awaiting QA re-verify.
Steps: pin in Noida (`28.535, 77.340`), or the map pin moved onto the Agra canal by Kalindi Kunj.
Expected: "This looks outside Delhi" plus general channels.
Actual: "We don't cover this area yet … These helplines take complaints from anywhere in Delhi", with MCD 311. The API's `out_of_coverage` doesn't say whether the pin was outside Delhi.

### B-019 · minor · backend: Hinglish landmark finds nothing (LOC-13)
**Status:** Fixed. Cause: Nominatim got the whole Hinglish sentence. One retry with the part after the last comma; the placeholder is now "e.g. Kalindi Kunj Park" (checked: Nominatim finds it). Awaiting QA re-verify.
Steps: landmark search `sarkari school ke saamne, Jaitpur` (the UI placeholder and the contract example).
Expected: some candidates (e.g. Jaitpur), or a ward-name fallback.
Actual: `{"candidates": []}` → "No place found". "Jaitpur" alone works. Nominatim gets the whole sentence.

### B-020 · minor · backend: zero-width text is accepted (IN-16)
**Status:** Fixed. Cause: `str.strip()` keeps U+200B. Text counts only if it has a printable, non-space character (`has_visible_text`), else 422 `need_photo_or_text`. Awaiting QA re-verify.
Steps: text = three U+200B characters, no photo.
Expected: 422 `need_photo_or_text`.
Actual: 201 and a dossier with an empty-looking quote. `str.strip()` keeps U+200B.

### B-021 · minor · frontend: pin moves on any map tap (LOC-19)
**Status:** Fixed. Cause: a map tap replaced the pin with no feedback. The tap keeps the old pin, and the composer shows "Pin moved N m · Undo". Awaiting QA re-verify.
Steps: pick the landmark "Kalindi Kunj", then tap the map once.
Expected: an explicit confirm, or a "moved X m" note.
Actual: the chip silently changes to "Pin on map". The report filed after that tap went to `out_of_coverage` (the pin had landed on the canal).

### B-022 · minor · backend + contract: names / house numbers not masked (SEC-03 ★)
**Status:** Fixed. Cause: no house-number rule. `HOUSE` masks `H.No. 45B`, `house no 12/3`, `flat 402`, `#12-B` → `… XX`. Names are not masked; the composer says "Don't include your name or address." and the contract lists it as a known gap. Awaiting QA re-verify.
Steps: text `mera naam Ramesh Kumar, H.No. 45B Gali 3`.
Expected (catalog SEC-03): masked in every shared output.
Actual: shown and quoted verbatim. Phones and emails are masked. The contract only names phones and emails, so this needs a decision.

### B-023 · minor · backend: no `nosniff` on media (SEC-11)
**Status:** Fixed. Cause: no header set. A small HTTP middleware adds `X-Content-Type-Options: nosniff` to every response. Awaiting QA re-verify.
`GET /api/v1/media/{id}` → `content-type: image/jpeg`, with no `X-Content-Type-Options` header. Re-encoding already removed the HTML from a polyglot JPEG; the header is the second layer.

### B-024 · minor · backend: API docs are public
**Status:** Fixed. Cause: FastAPI's docs are on by default. `openapi_url` is None unless `APP_ENV=local`, which turns off `/docs`, `/redoc` and `/openapi.json`. Awaiting QA re-verify.
`GET /docs` and `/openapi.json` → 200 without a token, so the full API map is public. Turn them off outside `APP_ENV=local`.

### B-025 · minor · devops/config: Streamlit binds to all interfaces
**Status:** Fixed. Cause: no `server.address`. `ui/.streamlit/config.toml` sets `127.0.0.1`; netstat now shows `127.0.0.1:8501` only. Awaiting QA re-verify.
`Get-NetTCPConnection` shows `:::8501`, and Streamlit prints Network/External URLs. The security checklist says local services listen on 127.0.0.1. Set `server.address = "127.0.0.1"` for local runs.

### B-026 · minor · frontend: dates in UTC
**Status:** Fixed. Cause: `created_at[:10]` of a UTC timestamp. `ist_date()` converts to IST (+05:30) first. Awaiting QA re-verify.
A report filed at ~02:30 IST on 29 Oct shows `2026-10-28` in My reports (`created_at[:10]` of a UTC timestamp). Show IST.

### B-027 · minor · backend: Hindi house number not masked (new, found in re-verify)
**Status:** Fixed. Cause: `HOUSE` only knew English keywords. It now also matches मकान नं./नंबर, म.नं., फ्लैट, मकान + number, makaan/makan no., ghar no. (Devanagari digits too); 9 test cases. Names stay a documented gap. Awaiting QA re-verify.
Steps: text `H.No. 45B Gali 3, house no 12/3, flat 402, #12-B, मकान नं. 45`.
Expected: every house/flat number masked.
Actual: `H.No. XX Gali 3, house no XX, flat XX, #XX, मकान नं. 45`. The English forms are masked; `मकान नं.` / `मकान नंबर` (and probably `फ्लैट`) are not.

### B-028 · minor · frontend: refresh drops the attached photo (new, found in re-verify)
**Status:** Fixed. Cause: only the token and text were kept in localStorage. The photo draft is now shrunk to a ≤1280 px JPEG on attach and kept as base64 (`cgr_photo`); over ~1.5 MB it is skipped and the composer says "Photo couldn't be kept after refresh, please re-attach." Sign out clears it. AppTests for both cases. Awaiting QA re-verify.
Steps: sign in, type text, attach a gallery photo, press F5.
Expected (catalog INF-07): the draft survives, photo included (it is the main input in the camera flow).
Actual: still signed in and the text is back, but the photo thumbnail is gone and has to be attached again.

### Re-verify, 29 Oct 2026 (Sprint 3 fixes, same methods as the first run)
ruff clean · pytest 121/121. API on 127.0.0.1:8010 and UI on 127.0.0.1:8501 (netstat).
- B-012 Verified: after F5 the user stays signed in and the draft text is back. 12 refreshes, then a fresh `dev-login` → 200, so restoring the token (`GET /me`, 15 × 200) doesn't count toward the limit. Sign out clears `cgr_token`/`cgr_draft`. The photo is not kept → B-028.
- B-013 Verified: client `location_verified=true` → notice `location_reported_by_phone` ("not verified"); a plain upload → `photo_unverified_location`. It is now stored only as the phone's claim.
- B-014 Verified: the 185/186/181/183 vertex (`28.5112908, 77.3124494`) and the 185/186 edge midpoint both → dossier, ward 186.
- B-015 Verified: 5 parallel same-spot posts → `[201, 429, 429, 429, 429]`; 5 parallel posts at different spots → 5 × 201.
- B-016 Verified: 375 px + 150 % text, photo attached: Remove right edge 326 px < card 351 px, the chip is 44 px tall, no horizontal scroll, and the sidebar is readable.
- B-017 Verified: `वार्ड १८५ में do gaddhe hain, call ९८७६५४३२१०` → `वार्ड १८५ में do gaddhe hain, call 98XXXXXX10` (citizen_text + letter).
- B-018 Verified: Noida (API, plus the UI via landmark "Noida Sector 18") → `outside_delhi: true`, no channels, card "This is outside Delhi" + Open CPGRAMS (pgportal.gov.in). Connaught Place and India Gate → `outside_delhi: false`, "We don't cover this area yet" + 155305.
- B-019 Verified: `sarkari school ke saamne, Jaitpur` → Jaitpur Road candidates; the new placeholder "Kalindi Kunj Park" also finds results.
- B-020 Verified: `"\u200b \u200b"` → 422 `need_photo_or_text`.
- B-021 Verified: a map tap after India Gate → "Pin moved 181 m" + Undo. Undo puts the pin back (28.61293, 77.22949) and the landmark chip returns.
- B-022 Verified: `H.No. 45B`, `house no 12/3`, `flat 402` and `#12-B` → `XX`. Names stay (documented gap; the composer says "Don't include your name or address."). Hindi form not masked → B-027.
- B-023 Verified: `X-Content-Type-Options: nosniff` on `/media` and `/healthz`.
- B-024 Verified: `/docs`, `/openapi.json` and `/redoc` → 404 with `APP_ENV=staging` (TestClient). They stay open on purpose with `APP_ENV=local`.
- B-025 Verified: Streamlit listens on 127.0.0.1:8501 only.
- B-026 Verified: reports filed at ~21:00 UTC on 28 Oct now show 2026-10-29.


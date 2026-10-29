# Sprint 3 API contract: location, privacy, login, limits

Extends `sprint2-api.md`; everything there still holds. Base `API_URL` (default `http://127.0.0.1:8010`), prefix `/api/v1`.

## Login (dev mode)
`AUTH_MODE=dev` (local only; the app refuses `dev` unless `APP_ENV=local`). Supabase email OTP replaces this in Sprint 8 without changing the endpoints.

### POST /api/v1/auth/dev-login
Body JSON: `{"name": "Priya", "email": "priya@example.com"}` (email is optional in dev mode)
Response `200`: `{"token": "<opaque dev token>", "user": {"id": "uuid", "name": "Priya"}}`

From now on, **every** `/reports`, `/dossiers`, `/media` and `/me/*` call needs the header `Authorization: Bearer <token>`.
Missing or invalid token → `401 {"error":{"code":"unauthorized","message_key":"login_required"}}`.
`/healthz` and `/auth/*` are the only endpoints that don't need a token (`/geocode` needs one).

### GET /api/v1/me
Response `200`: `{"user": {"id": "uuid", "name": "Priya"}}`; bad or expired token → 401.
The UI keeps the token in the browser's `localStorage` (with the composer draft text), so a
refresh doesn't sign the user out. On load it checks a kept token with this one call. `/me`
creates nothing, so it is **not** under the dev-login rate limit. Sign out wipes both.
Accepted risk: script running in the page (XSS) could read a `localStorage` token. Streamlit
escapes our text and the token is short-lived (7 days); Supabase sessions replace it in Sprint 8.

The dossier and the media can be read only by the user who reported them (other users get `404 not_found`, which hides that the item exists).

## POST /api/v1/reports: new and changed fields
| Field | Type | Rules |
|---|---|---|
| `lat`, `lng` | float | **Required from S3** (pin confirmed on the map). A missing pin → 422 `need_pin` |
| `accuracy_m` | float | optional; GPS accuracy in metres from the browser, 0–100000 (else 422 `invalid_input`) |
| `pin_source` | `gps` \| `gps_moved` \| `landmark` \| `map_click` | how the pin was set |
| `location_verified` | bool | the phone's **claim** that the photo came from the in-app camera |

**Accepted limitation (B-013):** the server can't check `location_verified`; any script can
send `true`. So it is stored as a claim only, it **never** affects routing (tested), and the
app never calls a photo "verified".

Response: unchanged shapes (dossier_ready / out_of_coverage). New `notices` values: `low_gps_accuracy` (accuracy > 50 m), and for every photo one of `location_reported_by_phone` (claim `true`: "Location reported by phone (not verified)") or `photo_unverified_location` (claim `false`, e.g. a gallery photo).

**Ward lookup:** `ST_Covers`, so a pin exactly on a ward edge or corner is inside. A pin in no
covered ward but within 15 m of one (`boundary_buffer_m`) gets the nearest covered ward.

**Out of coverage** now also says if the pin is outside Delhi (checked against Delhi's outer
edge, `backend/app/routing/delhi_outline.geojson`). Outside Delhi there are no channels (our
helplines are Delhi's); the UI points to CPGRAMS instead.
```json
"next": {"kind": "general_channels", "outside_delhi": true, "channels": []}
```
The no-pin fake ward 185 is **removed** (TL review item 7).

## POST /api/v1/geocode
Needs login (`Authorization: Bearer …`), so it can't be abused as an open Nominatim proxy.
Body JSON: `{"text": "sarkari school ke saamne, Jaitpur"}` (≤ 200 chars)
Response `200`: `{"candidates": [{"lat": 28.50, "lng": 77.32, "label": "Govt School, Jaitpur, …", "source": "nominatim"}]}` (≤ 5, limited to the Delhi bounding box). Empty list if nothing is found.
If the full text finds nothing, it retries **once** with only the part after the last comma
(`"sarkari school ke saamne, Jaitpur"` → `"Jaitpur"`): Nominatim can't read Hinglish phrases.

## GET /api/v1/me/reports
Response `200`: `{"reports": [{"report_id","state","category","ward_no","department_code","created_at","dossier_id"}]}`, newest first. Used by the "My issues" tab.

## Privacy rules (server side)
- **Photos:** EXIF/GPS metadata is stripped before saving. The original stays private (owner only). A **blurred copy** (faces + number plates, OpenCV) is what the dossier and share views use. If blurring fails, the photo is **withheld** from shared outputs.
- **Text:** stored **encrypted** (Fernet, `PII_KEY`). Phone numbers, emails and house/flat numbers (`H.No. 45B` → `H.No. XX`, `flat 402`, `#12-B`) in the text, including Devanagari digits and Hindi or English number words, are **masked** in the dossier (`citizen_text` and the English `complaint.en`). Everything else in `citizen_text` stays exactly as written (Devanagari digits too). Text with no visible characters (only spaces / zero-width characters) counts as no text.
  Known gaps: a number that mixes digits and words with fewer than 9 words is not masked. **Names are not masked** (no regex can find names reliably); the composer asks "Don't include your name or address."

## Rate limits
| Rule | Error |
|---|---|
| > 10 reports per user in a rolling 24 h (not a calendar day) | 429 `rate_limited_daily` |
| Same user, same category, within 50 m, within 24 h | 429 `rate_limited_same_spot` |
| 3 junk/rejected photos in 10 min → 15 min cooldown | 429 `rate_limited_cooldown` (used from Sprint 4, when real junk detection exists) |

| > 10 `POST /auth/dev-login` calls from one IP in 10 min (sliding window, per server process) | 429 `rate_limited_login` |

The report limits are checked inside the report's transaction after
`pg_advisory_xact_lock(hashtext(user_id))`, so one user's parallel posts run one at a time and
each sees the ones before it (5 parallel same-spot posts → 1 × 201, 4 × 429).

All of these use `code: "rate_limited"`; the specific rule is in `message_key`, e.g. `{"error": {"code": "rate_limited", "message_key": "rate_limited_daily"}}`.

## New error keys (UI must have English text for each)
`login_required` (401) · `need_pin` (422) · `rate_limited_daily` · `rate_limited_same_spot` · `rate_limited_cooldown` · `rate_limited_login` (429) · `geocode_failed` (502)

## Headers and docs
- Every response has `X-Content-Type-Options: nosniff`.
- `/docs`, `/redoc` and `/openapi.json` exist only with `APP_ENV=local`.

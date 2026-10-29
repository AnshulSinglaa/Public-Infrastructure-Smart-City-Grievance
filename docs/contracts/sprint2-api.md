# Sprint 2 API contract (thin slice)

Shared by the backend (`backend/`) and the UI (`ui/`). Base URL: `API_URL` (default `http://127.0.0.1:8010`), prefix `/api/v1`.
Sprint 2 uses **fakes** for AI and routing. The shapes below stay the same when the real parts arrive (ARCHITECTURE §7).

## POST /api/v1/reports  (multipart/form-data)
| Field | Type | Rules |
|---|---|---|
| `photo` | file (jpeg/png) | optional in S2, max 1 file, ≤ 5 MB |
| `text` | string | optional, ≤ 1000 chars; **photo or text required** |
| `lat`, `lng` | float | optional in S2; send both or neither |
| `ui_lang` | `hi` \| `en` | default `hi` |

Response `201`:
```json
{
  "report_id": "uuid",
  "state": "dossier_ready",
  "next": null,
  "master_id": "uuid",
  "dossier_id": "uuid",
  "notices": ["fake_ai"]
}
```
**Out of coverage** (pin in no ward, or in a ward not `in_coverage`; ARCHITECTURE §4 step 1): still `201`, but no department claim and no dossier. The report is saved with this state.
```json
{
  "report_id": "uuid",
  "state": "out_of_coverage",
  "next": {"kind": "general_channels",
           "channels": [{"type": "helpline", "label": "MCD 311", "value": "155305", "deep_link": "tel:155305"}]},
  "master_id": null,
  "dossier_id": null,
  "notices": ["fake_ai"]
}
```
`general_channels` = every contact with `scope = 'all'`. **No pin** → the S2 fake: ward 185, `routing.fake = true` (the UI sends no pin yet).

## Errors
Every error has this shape. `message_key` is what the UI turns into text (`ui/i18n.py` has English text for each one, UX §14; an unknown key shows a generic message).
```json
{"error": {"code": "validation", "message_key": "need_photo_or_text"}}
```
| Status | `code` | `message_key` | When |
|---|---|---|---|
| 422 | `validation` | `need_photo_or_text` | no photo and no (non-blank) text |
| 422 | `validation` | `too_many_photos` | more than one `photo` part |
| 422 | `validation` | `text_too_long` | text > 1000 chars |
| 422 | `validation` | `need_lat_and_lng` | only one of `lat` / `lng` |
| 422 | `validation` | `photo_too_large` | photo > 5 MB |
| 422 | `validation` | `photo_not_jpeg_png` | photo bytes are not JPEG/PNG |
| 422 | `validation` | `invalid_input` | FastAPI's own checks (bad float, lat > 90, bad uuid, …). Adds `"detail": {"fields": ["body.lat"]}` |
| 404 | `not_found` | `dossier_not_found` | unknown dossier id |
| 404 | `not_found` | `media_not_found` | unknown media id, or the file is gone |
| 404 | `not_found` | `not_found` | unknown route |
| 405 etc. | `http_error` | `server_error` | any other Starlette HTTP error (wrong method, …) |
| 500 | `server_error` | `server_error` | unhandled bug; no details in the body (they go to the server log) |

## GET /api/v1/dossiers/{dossier_id}
Response `200` (simplified Dossier from ARCHITECTURE §7):
```json
{
  "id": "uuid",
  "category": "pothole",
  "severity": "high",
  "pin": {"lat": 28.544, "lng": 77.311, "ward_no": 185, "ward_name": "Madanpur Khadar East"},
  "department": {"code": "MCD", "name_en": "Municipal Corporation of Delhi", "name_hi": "दिल्ली नगर निगम"},
  "routing": {"confidence_label": "very_likely", "curated": true, "fake": true},
  "complaint": {"en": "…"},
  "citizen_text": "सड़क पर बड़ा गड्ढा hai, call 98XXXXXX10",
  "claims": [],
  "deadline": {"sla_days": 15, "label": "suggested"},
  "reporter_count": 1,
  "channels": [{"type": "helpline", "label": "MCD 311", "value": "155305", "deep_link": "tel:155305"}],
  "photo_url": "/api/v1/media/{id}"
}
```
**English only (UX §14, from S3):** `complaint` has only `en`. `citizen_text` is the citizen's own text exactly as written (English, Hindi or Hinglish) with phone numbers/emails masked, or `null` for a photo-only report; the English letter quotes it as written.

## GET /api/v1/media/{media_id}
Returns the stored photo bytes (local disk under `media/`, git-ignored). No signing in S2.

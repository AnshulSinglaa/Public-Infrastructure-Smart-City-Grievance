# Deploy API contract: email-code login (AUTH_MODE=supabase)

Replaces the Sprint 3 dev login on the hosted app. Everything else (every `/reports`,
`/dossiers`, `/media`, `/me/*` call with `Authorization: Bearer <token>`, the 401 shape,
`GET /me`) is unchanged. `POST /auth/dev-login` still exists, but only with `AUTH_MODE=dev`,
and the API refuses to start with `dev` unless `APP_ENV=local`. With `supabase`, dev-login
is 404 and dev tokens are 401.

## POST /api/v1/auth/otp
Request: `{"email": "priya@example.com", "captcha": "<Turnstile token>"}`
Response `200`: `{"sent": true}`: Supabase emails a 6-digit code (valid ~1 h).

| When | Response |
|---|---|
| Turnstile token missing, used or wrong | 400 `captcha_failed` |
| > 5 codes for one email, or > 100 for the whole site, in 10 min | 429 `rate_limited_login` |
| Supabase's own email limit hit | 429 `rate_limited_login` |
| Supabase down or misconfigured | 502 `login_failed` |

## POST /api/v1/auth/verify
Request: `{"email": "priya@example.com", "code": "123456", "name": "Priya"}`
Response `200`: `{"token": "<Supabase access token (JWT)>", "user": {"id": "uuid", "name": "Priya"}}`
(the same shape as dev-login). `user.id` is the Supabase user id.

| When | Response |
|---|---|
| Wrong or expired code | 400 `bad_code` |
| > 10 tries for one email in 10 min | 429 `rate_limited_login` |

## The token
- The API checks every token with Supabase's **public** keys (`<SUPABASE_URL>/auth/v1/.well-known/jwks.json`,
  ES256/RS256 only), plus `aud = authenticated`, `iss = <SUPABASE_URL>/auth/v1` and expiry.
  So the API holds no JWT secret.
- It lasts **1 hour**. After that any call is 401 `login_required` and the UI shows the sign-in card.
- **SEC-005 (changed):** the UI keeps this token only in `st.session_state`, which lives on the
  Streamlit server. It is never written to the browser's localStorage, so script in the page
  can't read it. A page refresh asks for a new code. (Dev mode still keeps its token in
  localStorage, but only on a laptop.)

## Login rate limits (SEC-004, changed)
Counted in the Postgres table `login_attempt` (migration 0007), not in server memory, so they
survive restarts and are shared by every worker. Emails are stored only as a SHA-256 hash.
Dev-login keeps its limit of 10 per IP per 10 minutes. The hosted UI calls the API from its
server, so every user has the same IP: that's why the email-code limits are per email.

New `message_key`s: `captcha_failed` (400), `bad_code` (400), `login_failed` (502).

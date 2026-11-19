# Deploy API contract: email + password login (AUTH_MODE=supabase)

Replaces the Sprint 3 dev login on the hosted app. Everything else (every `/reports`,
`/dossiers`, `/media`, `/me/*` call with `Authorization: Bearer <token>`, the 401 shape,
`GET /me`) is unchanged. `POST /auth/dev-login` still exists, but only with `AUTH_MODE=dev`,
and the API refuses to start with `dev` unless `APP_ENV=local`. With `supabase`, dev-login
is 404 and dev tokens are 401.

No email is ever sent, so no SMTP sender is needed. The Supabase project must have
**Authentication -> Providers -> Email -> "Confirm email" switched OFF**, so a sign-up returns
a session straight away. (The earlier email-code routes `/auth/otp` and `/auth/verify` are
removed: they are 404.)

## POST /api/v1/auth/signup
Request: `{"email": "priya@example.com", "password": "at-least-8-chars", "name": "Priya", "captcha": "<Turnstile token>"}`
(`password` 8 to 72 characters, `name` 1 to 80.)
Response `200`: `{"token": "<Supabase access token (JWT)>", "user": {"id": "uuid", "name": "Priya"}}`
(the same shape as dev-login). `user.id` is the Supabase user id.

| When | Response |
|---|---|
| Body invalid (short password, bad email, empty name) | 422 `invalid_input` |
| Turnstile token missing, used or wrong | 400 `captcha_failed` |
| That email already has an account | 409 `email_taken` |
| Supabase rejects the password as too easy to guess | 400 `weak_password` |
| > 5 sign-ups for one email, or > 100 for the whole site, in 10 min | 429 `rate_limited_login` |
| Supabase's own rate limit hit | 429 `rate_limited_login` |
| Supabase returned no session ("Confirm email" is still ON) | 503 `signup_unavailable` |
| Supabase down, slow or misconfigured | 502 `login_failed` |

## POST /api/v1/auth/login
Request: `{"email": "priya@example.com", "password": "...", "captcha": "<Turnstile token>"}`
Response `200`: same as sign-up. `user.name` is the name stored at sign-up (the part of the
email before the `@` if there is none).

| When | Response |
|---|---|
| Turnstile token missing, used or wrong | 400 `captcha_failed` |
| Wrong password or unknown email (the same answer for both) | 400 `bad_credentials` |
| > 10 tries for one email in 10 min | 429 `rate_limited_login` |
| Supabase down, slow or misconfigured | 502 `login_failed` |

## The token
- The API checks every token with Supabase's **public** keys (`<SUPABASE_URL>/auth/v1/.well-known/jwks.json`,
  ES256/RS256 only), plus `aud = authenticated`, `iss = <SUPABASE_URL>/auth/v1` and expiry
  (`exp` is required). `iat` is not checked, so a few seconds of clock difference between
  Supabase and the API can't reject a brand-new token. A trailing `/` in `SUPABASE_URL` is
  ignored. So the API holds no JWT secret.
- It lasts **1 hour**. After that any call is 401 `login_required` and the UI shows the sign-in card.
- **SEC-005 (changed):** the UI keeps this token only in `st.session_state`, which lives on the
  Streamlit server. It is never written to the browser's localStorage, so script in the page
  can't read it. A page refresh asks to sign in again. (Dev mode still keeps its token in
  localStorage, but only on a laptop.)
- The password is sent to the API over HTTPS and passed straight on to Supabase. The API never
  stores or logs it; Supabase keeps only its hash.

## Login rate limits (SEC-004, changed)
Counted in the Postgres table `login_attempt` (migration 0007), not in server memory, so they
survive restarts and are shared by every worker. Emails are stored only as a SHA-256 hash.
Dev-login keeps its limit of 10 per IP per 10 minutes. The hosted UI calls the API from its
server, so every user has the same IP: that's why the limits are per email (plus one site-wide
cap on sign-ups).

New `message_key`s: `captcha_failed` (400), `bad_credentials` (400), `weak_password` (400),
`email_taken` (409), `signup_unavailable` (503), `login_failed` (502).

# Deploy runbook: Civic Grievance Router (free tier)

Goal: a live demo link. Everything is free; every secret is typed into a dashboard by you,
never into git and never into a chat.

## 1. What runs where

```
  Citizen's browser
        │ HTTPS
        ▼
 ┌────────────────────────────┐  email code  ┌──────────────────────────┐
 │ Streamlit Community Cloud  │◄────────────►│ Cloudflare Turnstile     │
 │ ui/app.py  (the UI)        │  (widget)    │ "are you human?" check   │
 └────────────────────────────┘              └──────────────────────────┘
        │ HTTPS, server to server, Bearer <Supabase token>        ▲ secret check
        ▼                                                         │
 ┌────────────────────────────┐───────────────────────────────────┘
 │ Render (free, Docker)      │── send code / verify code ──► Supabase Auth (email OTP)
 │ FastAPI  infra/api/        │── JWT public keys (JWKS) ───► Supabase Auth
 │ Dockerfile, /healthz       │── photo + text ─────────────► Gemini, Groq
 └────────────────────────────┘
        │ SQL (session pooler, SSL)        │ HTTPS + API key
        ▼                                  ▼
 ┌────────────────────────────┐   ┌──────────────────────────┐
 │ Supabase Postgres + PostGIS│   │ Qdrant Cloud (vectors)   │
 └────────────────────────────┘   └──────────────────────────┘
        ▲
        │ GitHub Actions: CI on every push · Keepalive every 10 min · Backup daily
```

| Service | Why this one |
|---|---|
| **Supabase** (Postgres + PostGIS + Auth) | Free Postgres with PostGIS, plus email-code login, in one account |
| **Qdrant Cloud** | Free 1 GB vector cluster; same Qdrant as local Docker |
| **Render** (Docker web service) | Free, runs our Dockerfile, HTTPS and health checks built in |
| **Streamlit Community Cloud** | Free, made for Streamlit apps, deploys from GitHub |
| **Cloudflare Turnstile** | Free, privacy-friendly bot check on login (no puzzles) |
| **GitHub Actions** | CI, warm-up pings, encrypted daily backups (free for public repos) |

## 2. Environment variables (names only)

| Where | Variables |
|---|---|
| **Render** (API) | `APP_ENV=prod`, `AUTH_MODE=supabase`, `PROVIDER_MODE=live` (set by `render.yaml`) · secrets: `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `TURNSTILE_SECRET`, `ALLOWED_ORIGINS`, `QDRANT_URL`, `QDRANT_API_KEY`, `GROQ_API_KEY`, `GOOGLE_API_KEY`, `PII_KEY` · optional: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` |
| **Streamlit Cloud** (UI, "Secrets" box) | `API_URL`, `AUTH_MODE`, `TURNSTILE_SITE_KEY` (none of these is a secret) |
| **GitHub** (Actions secrets) | `API_URL` (warm-up), `DATABASE_URL` + `BACKUP_PUBLIC_KEY` (backup; public key only) |

**Safety built in:** with `APP_ENV=prod` the API refuses to start if any secret above is
missing or weak (e.g. `dev`, `change-me`, a `127.0.0.1` database, a Cloudflare test key). The
Render log then lists the bad *names* (never the values). `/docs` is off, errors are generic,
dev login is off, every response has security headers + HSTS (HTTPS only), and CORS allows
only `ALLOWED_ORIGINS`.

## 3. What you (the user) do

Do these in order. Keep a password manager (or a private note) open: you'll create a few
values once and paste them into dashboards. Never paste them into chat, issues or git.

**A. Make a secret and a backup key on your laptop** (PowerShell, in the repo folder):
1. `uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` → save as **PII_KEY**. Keep it forever: without it, saved report text can't be read.
2. Backup keypair (gpg comes with Git for Windows; or install Gpg4win): `gpg --quick-generate-key "civic-backup" default default never` (pick a passphrase when asked). Then `gpg --armor --export civic-backup > backup-public.asc`: this **public** key is what GitHub gets (step 21). The **private** key stays in your keyring; also keep an offline copy: `gpg --armor --export-secret-keys civic-backup > backup-private.asc` → move it to a USB stick / password manager, never into the repo folder.

**B. Push the code** (the dashboards read `render.yaml` and `ui/app.py` from GitHub):

3. `git status` → check that no `.env` file is listed.
4. `git add -A` → `git commit -m "Deploy mini-sprint: Supabase login, Turnstile, Docker, backups"` → `git push`.
5. GitHub → **Actions** → wait for **CI** to go green.

**C. Supabase** ([supabase.com](https://supabase.com), sign in with GitHub):

6. **New project** → name `civic-grievance`, region **Mumbai (ap-south-1)**, generate a strong database password (save it).
7. **Connect** (top bar) → **Session pooler** → copy the URI, put your password in it, add `?sslmode=require` at the end → save as **DATABASE_URL**.
8. **Project Settings → API Keys** → copy the **Project URL** (`https://<ref>.supabase.co`) → **SUPABASE_URL**, and the **anon / publishable** key → **SUPABASE_ANON_KEY**. (Never use the `service_role` / secret key anywhere.)
9. **Project Settings → JWT Keys** → the current key should be **ECC (P-256)** (default for new projects). If it says "Legacy HS256", click to migrate/rotate to the new signing keys.
10. **Authentication → Emails → Templates**: in both **Magic Link** and **Confirm signup**, replace the link with the code: `Your sign-in code is {{ .Token }}`. Save.
11. **Authentication → URL Configuration** → Site URL = your Streamlit URL (step 18).
12. *Optional, so people other than you get codes:* Supabase's built-in mailer only sends a few emails an hour, to your own team's addresses. **Authentication → Emails → SMTP Settings** → add a free SMTP sender (for example Brevo, which allows a single verified sender address).

**D. Load the database** (from your laptop, one command):

13. In PowerShell: `$env:DATABASE_URL = Read-Host "Paste DATABASE_URL"` (paste, Enter), then
    `uv run python scripts/load_all.py` → it ends with `Done: database ready.` Then `Remove-Item Env:DATABASE_URL`.

**E. Qdrant Cloud** ([cloud.qdrant.io](https://cloud.qdrant.io)):

14. **Create a free cluster** (region near India if offered, version 1.x). Copy the cluster URL (`https://….cloud.qdrant.io:6333`) → **QDRANT_URL**.
15. Cluster → **API Keys** → create one → **QDRANT_API_KEY**.

**F. Cloudflare Turnstile** ([dash.cloudflare.com](https://dash.cloudflare.com) → **Turnstile**):

16. **Add widget** → name `civic-login`, hostname = the Streamlit subdomain you will pick in step 18 (e.g. `civic-grievance-router.streamlit.app`), mode **Managed**.
17. Copy the **Site key** → **TURNSTILE_SITE_KEY** and the **Secret key** → **TURNSTILE_SECRET**.

**G. Streamlit Community Cloud** ([share.streamlit.io](https://share.streamlit.io), sign in with GitHub):

18. **Create app** → this repo, branch `main`, main file `ui/app.py`, **App URL** = the subdomain from step 16. **Advanced settings** → Python **3.12**, and in **Secrets** paste (with your Render URL from step 20; edit it later if you don't have it yet):
    ```toml
    API_URL = "https://civic-api-xxxx.onrender.com"
    AUTH_MODE = "supabase"
    TURNSTILE_SITE_KEY = "<site key>"
    ```
    → **Deploy**.

**H. Render** ([render.com](https://render.com), sign in with GitHub):

19. **New → Blueprint** → pick this repo → Render reads `render.yaml` and asks for each secret: paste DATABASE_URL, SUPABASE_URL, SUPABASE_ANON_KEY, TURNSTILE_SECRET, QDRANT_URL, QDRANT_API_KEY, GROQ_API_KEY, GOOGLE_API_KEY, PII_KEY, and **ALLOWED_ORIGINS** = `https://<your-subdomain>.streamlit.app` → **Apply**.
20. When it's live, open `https://civic-api-xxxx.onrender.com/healthz?db=1` → `{"ok":true,...}`. Copy that base URL → **API_URL** (and update the Streamlit secret if needed: app → ⋮ → **Settings → Secrets**).

**I. GitHub secrets** (repo → **Settings → Secrets and variables → Actions → New repository secret**):

21. Add `API_URL`, `DATABASE_URL`, and `BACKUP_PUBLIC_KEY` = the whole text of `backup-public.asc` (from `-----BEGIN PGP PUBLIC KEY BLOCK-----` to the END line). Then delete `backup-public.asc` from the repo folder.
22. **Actions → Keepalive → Run workflow**, then **Backup → Run workflow** → both green; the Backup run has a `db-backup-N` artifact.

**J. Smoke test** (on your phone too):

23. Open the Streamlit URL → see the demo banner → name + email → Turnstile tick → **Email me a code** → type the code → signed in.
24. **Location → Try a demo spot → "Pothole on Ring Road, Lajpat Nagar"** → write "big pothole" → send → the dossier says **PWD**. Try the other spots (MCD, NHAI, TPDDL).

## 4. Day-to-day

- **Deploy a change:** push to `main`. CI runs; Render redeploys only after CI is green (`autoDeployTrigger: checksPass`). Streamlit redeploys by itself.
- **New migration or data change:** run step 13 again (safe to re-run).
- **Logs:** Render → service → **Logs** (API); Streamlit → app → **Manage app** (UI); Langfuse for AI calls. A failed scheduled workflow emails you.
- **Before a demo:** open the Streamlit URL 5 minutes early (it sleeps after ~12 h without visitors) and hit `/healthz?db=1` once.

## 5. Backups

Supabase's free plan has no downloadable backups, so `.github/workflows/backup.yml` runs
`pg_dump` every night (02:00 IST): only the `public` schema (our tables, never Supabase's
`auth` schema with emails), **encrypted to your backup public key before upload**, kept 7
days. The repo is public, so anyone signed in to GitHub can download the artifact, but only
your private key (on your machine, never on GitHub) can open it. The job fails if the key
secret is missing. Report text and names inside are also Fernet-encrypted.

Restore (download the artifact zip from the Actions run, unzip):
```powershell
gpg --output backup.dump --decrypt backup.dump.gpg   # uses your private key; asks its passphrase
docker run --rm -v "${PWD}:/b" -e DATABASE_URL postgres:17-alpine pg_restore --no-owner --clean --if-exists -d "$env:DATABASE_URL" /b/backup.dump
```
(On another computer, first `gpg --import backup-private.asc`. Restore into a fresh project: run step 13 first so PostGIS and the tables exist.)

## 6. Known limits of the free demo

- **Cold start:** Render free sleeps after 15 min idle; the first visit then waits ~1 min. Keepalive pings 07:30–23:30 IST to avoid it.
- **Photos** are stored on Render's disk, which is wiped on each deploy (Supabase Storage comes later).
- **Sign-in lasts 1 hour**, and a page refresh asks for a new code (the token stays on the server, SEC-005).
- **Coverage:** 25 Delhi wards. Elsewhere the app shows official helplines, so use the demo spots.
- If the Streamlit app never loads, delete the `address` line under `[server]` in `ui/.streamlit/config.toml` (it is there to keep local runs private).

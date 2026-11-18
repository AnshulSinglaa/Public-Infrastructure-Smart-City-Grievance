import logging
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives at the project root, two folders above this file (backend/app/config.py).
ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    database_url: str = "postgresql://postgres:dev@127.0.0.1:5433/civic"
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: SecretStr | None = None  # only for Qdrant Cloud; local Docker needs none

    # SecretStr hides the value in print/repr/logs: it shows as '**********'.
    groq_api_key: SecretStr = SecretStr("")
    google_api_key: SecretStr = SecretStr("")  # Gemini
    text_model: str = "openai/gpt-oss-120b"
    vision_model: str = "gemini-3.5-flash-lite"
    provider_mode: str = "live"  # live = real models; fake = scripted answers (tests)
    llm_timeout_s: int = 20  # per request; see the time budget in providers/common.py
    llm_cache_dir: Path = ROOT / ".cache" / "llm"
    # LLM gateway (app/llm/gateway.py): Groq first, this Gemini model when Groq fails.
    # Groq free tier (headers, 2026-11-15): gpt-oss-120b, gpt-oss-20b and qwen3.8-27b all
    # allow 8,000 tokens/min and 1,000 requests/day, so the limits don't favour a smaller
    # model: Q&A keeps gpt-oss-120b (reasoning effort low), and the prompt is cut to <= 1.5k.
    fallback_model: str = "gemini-2.5-flash-lite"  # stable GA text model, no thinking by default
    fallback_timeout_s: int = 20  # Gemini answers in 5-8 s, and its 503s are often slow
    intent_model: str = "openai/gpt-oss-20b"  # small Groq model for borderline intents (B-057)
    groq_tpm: int = 8000  # tokens per minute per Groq model (free tier): our own throttle
    gemini_rpm: int = 10  # requests per minute for the Gemini fallback (free tier, with margin)
    embed_model: str = "gemini-embedding-001"
    embed_dims: int = 768
    qa_timeout_s: int = 8  # Groq per request for Q&A answers (intake uses llm_timeout_s)
    # Sprint 6b Q&A + home chat
    chunks_collection: str = "rule_chunks"  # Qdrant alias (dense + bm25 named vectors)
    rerank: bool = True  # RERANK=off skips the MiniLM cross-encoder (memory, HLD §8)
    rate_questions_report: int = 10
    rate_questions_day: int = 40  # shared by /chat and /ask; fixed replies count 1/4
    qa_site_day: int = 1000  # smallest daily free quota we depend on (Groq big model RPD)
    qa_site_tokens_day: int = 200_000  # Groq's daily token limit for the Q&A model (docs)
    qa_quota_guard: float = 0.8  # stop new questions at 80 % of either -> 429 qa_busy
    rate_questions_new_user: int = 10  # accounts younger than 24 h (REVIEW B-13)
    intent_hi: float = 0.80  # cosine to the example bank: >= HI and a margin -> that label
    intent_lo: float = 0.40  # every label below LO -> off_domain
    intent_margin: float = 0.05
    langfuse_public_key: str = ""  # empty = tracing off
    langfuse_secret_key: SecretStr = SecretStr("")
    langfuse_host: str = "https://cloud.langfuse.com"

    app_env: str = "local"  # local | prod
    auth_mode: str = "dev"  # dev = /auth/dev-login (local only) | supabase = email OTP
    supabase_url: str = ""  # https://<project>.supabase.co
    supabase_anon_key: SecretStr = SecretStr("")  # sends the OTP email (public-safe key)
    turnstile_secret: SecretStr = SecretStr("")  # Cloudflare Turnstile; empty = off (local)
    allowed_origins: str = ""  # comma list for CORS, e.g. https://<app>.streamlit.app
    dev_token_secret: SecretStr = SecretStr("")  # empty + local -> generated into .cache/
    dev_token_days: int = 7
    pii_key: SecretStr = SecretStr("")  # Fernet key for report text / user names
    media_dir: Path = ROOT / "media"  # uploaded photos (git-ignored)
    ask_threshold: float = 0.7
    dedup_radius_m: int = 50
    road_snap_m: int = 25
    boundary_buffer_m: int = 15
    resolution_radius_m: int = 30
    photo_max_px: int = 1280
    gps_accuracy_max_m: float = 50
    rate_reports_day: int = 10
    rate_answers_report: int = 5   # free-text /answer model calls per report (SEC-015)
    rate_answers_day: int = 30     # ... and per user in 24 h; button answers are free
    rate_same_spot_h: int = 24
    rate_same_spot_m: int = 50


settings = Settings()

# Values that only make sense on a laptop. In prod any of them stops the app (SEC-007).
WEAK = {"", "dev", "change-me", "postgres", "password"}
TURNSTILE_TEST_SECRETS = {"1x0000000000000000000000000000000AA",
                          "2x0000000000000000000000000000000AA",
                          "3x0000000000000000000000000000000AA"}


def prod_problems(s: Settings = settings) -> list[str]:
    """Names (never values) of settings that are missing or unsafe outside APP_ENV=local."""
    if s.app_env == "local":
        return []
    secret = {"GROQ_API_KEY": s.groq_api_key, "GOOGLE_API_KEY": s.google_api_key,
              "PII_KEY": s.pii_key, "SUPABASE_ANON_KEY": s.supabase_anon_key,
              "TURNSTILE_SECRET": s.turnstile_secret, "QDRANT_API_KEY": s.qdrant_api_key}
    bad = [k for k, v in secret.items() if v is None or v.get_secret_value() in WEAK]
    if s.turnstile_secret.get_secret_value() in TURNSTILE_TEST_SECRETS:
        bad.append("TURNSTILE_SECRET (Cloudflare test key)")
    if "127.0.0.1" in s.database_url or ":dev@" in s.database_url:
        bad.append("DATABASE_URL")
    for name, url in (("SUPABASE_URL", s.supabase_url), ("QDRANT_URL", s.qdrant_url)):
        if not url.startswith("https://"):
            bad.append(name)
    if s.auth_mode != "supabase":
        bad.append("AUTH_MODE (must be supabase)")
    if s.provider_mode != "live":
        bad.append("PROVIDER_MODE (must be live)")
    return bad


def check_prod() -> None:
    """Called at startup. Prod with a weak or missing secret refuses to start."""
    if bad := prod_problems():
        raise RuntimeError("Unsafe settings for APP_ENV=" + settings.app_env + ": "
                           + ", ".join(bad))


def local_secret(filename: str, make) -> str:
    """Dev-only secret kept in .cache/ (git-ignored): made once by make(), then reused,
    so tokens and encrypted rows survive restarts."""
    path = ROOT / ".cache" / filename
    if not path.exists():
        path.parent.mkdir(exist_ok=True)
        path.write_text(make(), encoding="utf-8")
    logging.getLogger(__name__).warning("Using dev secret .cache/%s (local only)", filename)
    return path.read_text(encoding="utf-8").strip()

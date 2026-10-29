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
    vision_model: str = "gemini-2.5-flash"

    app_env: str = "local"
    auth_mode: str = "dev"  # dev = /auth/dev-login (local only); Supabase OTP from Sprint 8
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
    rate_same_spot_h: int = 24
    rate_same_spot_m: int = 50


settings = Settings()


def local_secret(filename: str, make) -> str:
    """Dev-only secret kept in .cache/ (git-ignored): made once by make(), then reused,
    so tokens and encrypted rows survive restarts."""
    path = ROOT / ".cache" / filename
    if not path.exists():
        path.parent.mkdir(exist_ok=True)
        path.write_text(make(), encoding="utf-8")
    logging.getLogger(__name__).warning("Using dev secret .cache/%s (local only)", filename)
    return path.read_text(encoding="utf-8").strip()

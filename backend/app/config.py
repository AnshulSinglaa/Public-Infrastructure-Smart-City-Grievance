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
    media_dir: Path = ROOT / "media"  # uploaded photos (git-ignored)
    ask_threshold: float = 0.7
    dedup_radius_m: int = 50
    road_snap_m: int = 25
    boundary_buffer_m: int = 15
    resolution_radius_m: int = 30


settings = Settings()

import psycopg

from app.config import settings


def connect(timeout: int = 3) -> psycopg.Connection:
    """Open a new connection. Use it as `with connect() as conn:` so it closes itself."""
    return psycopg.connect(settings.database_url, connect_timeout=timeout)

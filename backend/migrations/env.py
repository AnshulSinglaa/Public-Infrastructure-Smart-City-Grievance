"""Alembic runs this file. We write migrations as plain SQL, so no models/metadata here."""

from alembic import context
from sqlalchemy import create_engine

from app.config import settings

# psycopg 3 driver; the .env URL is a plain postgresql:// URL.
url = settings.database_url.replace("postgresql://", "postgresql+psycopg://", 1)

with create_engine(url).connect() as conn:
    context.configure(connection=conn)
    with context.begin_transaction():
        context.run_migrations()

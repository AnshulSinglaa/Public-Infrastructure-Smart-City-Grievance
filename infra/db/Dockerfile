# Postgres 16 with pgvector (official image, Debian bookworm) + PostGIS from apt.
# The postgis/postgis:16-* images are on Debian bullseye, whose Postgres apt repo was retired,
# so adding pgvector to them fails. Going the other way round works.
FROM pgvector/pgvector:pg16
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-16-postgis-3 \
    && rm -rf /var/lib/apt/lists/*

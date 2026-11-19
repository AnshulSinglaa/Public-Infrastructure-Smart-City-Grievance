# Postgres 16 with pgvector (official image, Debian bookworm) + PostGIS from apt.
# The postgis/postgis:16-* images are on Debian bullseye, whose Postgres apt repo was retired,
# so adding pgvector to them fails. Going the other way round works.
FROM pgvector/pgvector:pg16@sha256:0dd3149a06e4579a0d70d8fef9945636cd514005e7237deb27f9b67886f8dc4f
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-16-postgis-3 \
    && rm -rf /var/lib/apt/lists/*

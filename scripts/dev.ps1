# Start the local database and mail sandbox.
docker compose -f "$PSScriptRoot/../infra/docker-compose.yml" up -d db mailpit qdrant

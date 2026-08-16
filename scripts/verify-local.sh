#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

for path in .env.example docker-compose.yml backend/requirements.txt backend/alembic.ini frontend/package.json; do
  [ -f "$path" ] || { echo "Missing required file: $path" >&2; exit 1; }
done
[ -f .env ] || { echo "Missing .env. Run: cp .env.example .env" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || { echo "Docker Desktop is required." >&2; exit 1; }
docker compose config --quiet

attempt=0
until curl --fail --silent http://localhost:8000/api/health >/tmp/marketmind-health.json; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 23 ] || { echo "Backend health endpoint did not respond." >&2; exit 1; }
  sleep 2
done
grep -q '"database":"connected"' /tmp/marketmind-health.json || { echo "Database is not connected." >&2; exit 1; }
curl --fail --silent http://localhost:3000 >/dev/null || { echo "Frontend did not respond." >&2; exit 1; }
echo "MarketMind local verification passed."

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
API_DIR="$PROJECT_DIR/apps/api"
PYLIBS="$API_DIR/pylibs"
DIST_DIR="$PROJECT_DIR/apps/web/dist"
PORT="${DEPLOY_RUN_PORT:-5000}"
API_PORT="${API_PORT:-8000}"

if [ ! -f "$DIST_DIR/index.html" ]; then
  echo "ERROR: $DIST_DIR/index.html not found. Run deploy-build.sh first." >&2
  exit 1
fi
if [ ! -d "$PYLIBS" ]; then
  echo "ERROR: $PYLIBS not found. Run deploy-build.sh first." >&2
  exit 1
fi

export PYTHONPATH="$PYLIBS"
cd "$API_DIR"

echo "Running database migrations..."
python3 -m alembic upgrade head

if [ -n "${ATI_ADMIN_PASSWORD:-}" ]; then
  echo "Ensuring admin user exists..."
  python3 -m app.bootstrap "${ATI_ADMIN_USERNAME:-admin}" "$ATI_ADMIN_PASSWORD" || true
else
  echo "ATI_ADMIN_PASSWORD not set; skip admin bootstrap."
fi

echo "Starting backend on 127.0.0.1:${API_PORT}..."
python3 -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" > /tmp/api.log 2>&1 &

for i in $(seq 1 20); do
  if curl -sf --max-time 2 "http://127.0.0.1:${API_PORT}/api/v1/health" >/dev/null 2>&1; then
    echo "Backend ready."
    break
  fi
  sleep 0.5
done

export API_PORT
exec node "$SCRIPT_DIR/static-server.mjs" "$DIST_DIR" "$PORT"

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="$PROJECT_DIR/apps/web/dist"
PYTHON_DEPS="$PROJECT_DIR/.python_deps"
PORT="${DEPLOY_RUN_PORT:-5000}"

if [ ! -f "$DIST_DIR/index.html" ]; then
  echo "ERROR: $DIST_DIR/index.html not found. Run deploy-build.sh first." >&2
  exit 1
fi
if [ ! -d "$PYTHON_DEPS" ]; then
  echo "ERROR: $PYTHON_DEPS not found. Run deploy-build.sh first." >&2
  exit 1
fi

export PYTHONPATH="$PYTHON_DEPS${PYTHONPATH:+:$PYTHONPATH}"
export PATH="$PYTHON_DEPS/bin:$PATH"
# config.py 的 pydantic settings 使用 ATI_ 前缀；DB 用绝对路径保证 alembic 与 app 连同一个库
export ATI_DATABASE_URL="sqlite:////tmp/signal_atlas.db"
export ATI_WEB_DIST="$DIST_DIR"

fuser -k "${PORT}/tcp" 2>/dev/null || true
sleep 1

cd "$PROJECT_DIR/apps/api"
python3 -m alembic upgrade head
python3 -m app.bootstrap admin "${ATI_BOOTSTRAP_PASSWORD:-PreviewAdmin!2026}"

exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"

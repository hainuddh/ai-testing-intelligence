#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# 后端 Python 依赖
cd "$PROJECT_DIR/apps/api"
if [ ! -f .venv/bin/python ]; then
  echo "Creating backend venv..."
  uv venv .venv
fi
echo "Installing backend deps..."
uv pip install -p .venv -e ".[dev]"
echo "Running database migrations..."
.venv/bin/alembic upgrade head

# 前端 Node 依赖
cd "$PROJECT_DIR/apps/web"
echo "Installing frontend deps..."
pnpm install --prefer-frozen-lockfile --prefer-offline

echo "Preview build complete."
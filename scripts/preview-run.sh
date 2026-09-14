#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

EXPOSE_PORT=$(awk -F '[ =]+' '/^expose_port/ {gsub(/[^0-9]/, "", $2); print $2; exit}' .preview 2>/dev/null || echo 5000)
export VITE_PORT="$EXPOSE_PORT"

# 清理残留（绝不碰 9000）
fuser -k "${EXPOSE_PORT}/tcp" >/dev/null 2>&1 || true

# 启动后端 API（内部 8000，供前端 proxy）
cd "$PROJECT_DIR/apps/api"
( nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 > /tmp/ati_api.log 2>&1 & )

# 启动前端 Vite dev server（对外暴露端口）
cd "$PROJECT_DIR/apps/web"
exec pnpm exec vite --host 0.0.0.0 --port "$EXPOSE_PORT"
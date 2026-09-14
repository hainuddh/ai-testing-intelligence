#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

DIST_DIR="$PROJECT_DIR/apps/web/dist"
PORT="${DEPLOY_RUN_PORT:-5000}"

if [ ! -f "$DIST_DIR/index.html" ]; then
  echo "ERROR: $DIST_DIR/index.html not found. Run deploy-build.sh first." >&2
  exit 1
fi

exec node "$SCRIPT_DIR/static-server.mjs" "$DIST_DIR" "$PORT"

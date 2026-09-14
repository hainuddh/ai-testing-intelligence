#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR/apps/web"

PORT="${DEPLOY_RUN_PORT:-5000}"

exec npx serve dist -l "$PORT"
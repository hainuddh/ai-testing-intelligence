#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR/apps/web"

echo "Installing dependencies..."
pnpm install --prefer-frozen-lockfile --prefer-offline

echo "Building frontend..."
pnpm run build

echo "Build complete. dist/ ready."
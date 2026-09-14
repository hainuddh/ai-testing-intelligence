#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_DEPS="$PROJECT_DIR/.python_deps"

echo "Installing dependencies..."
cd "$PROJECT_DIR/apps/web"
pnpm install --prefer-frozen-lockfile --prefer-offline

echo "Building frontend..."
pnpm run build

echo "Installing backend dependencies (target: .python_deps)..."
cd "$PROJECT_DIR/apps/api"
if command -v uv >/dev/null 2>&1; then
  uv pip install -p python3 --target "$PYTHON_DEPS" ".[dev]" \
    --index-url https://mirrors.aliyun.com/pypi/simple/
elif command -v pip3 >/dev/null 2>&1; then
  pip3 install --target "$PYTHON_DEPS" ".[dev]" --no-build-isolation \
    --index-url https://mirrors.aliyun.com/pypi/simple/
else
  echo "ERROR: no uv/pip3/pip found in deploy build environment" >&2
  exit 1
fi

echo "Build complete. dist/ and .python_deps/ ready."

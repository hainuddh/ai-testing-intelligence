#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
API_DIR="$PROJECT_DIR/apps/api"
PYLIBS="$API_DIR/pylibs"

cd "$PROJECT_DIR/apps/web"

echo "Installing frontend dependencies..."
pnpm install --prefer-frozen-lockfile --prefer-offline

echo "Building frontend..."
pnpm run build

echo "Installing backend dependencies..."
cd "$API_DIR"
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 runtime not found" >&2; exit 1; }
python3 -m pip --version >/dev/null 2>&1 || python3 -m ensurepip --upgrade
set -f
DEPS="$(python3 -c "import tomllib; print(' '.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))")"
set +f
python3 -m pip install --no-cache-dir --target "$PYLIBS" $DEPS \
  -i https://mirrors.aliyun.com/pypi/simple/

echo "Build complete. dist/ and pylibs/ ready."

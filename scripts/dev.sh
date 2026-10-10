#!/usr/bin/env bash
set -euo pipefail
# Give each background job its own process group, including make/npm children.
set -m

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON="${PYTHON:-$ROOT_DIR/.venv/bin/python}"
NPM="${NPM:-npm}"

if [[ ! -x "$PYTHON" ]]; then
  echo "Missing Python virtual environment at .venv/."
  echo "Run: make bootstrap"
  exit 1
fi

if [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
  echo "Missing frontend dependencies. Install them with:"
  echo "  cd frontend && npm install"
  exit 1
fi

# Select once and share the result with both the API and Vite's proxy.
API_PORT="$("$PYTHON" "$ROOT_DIR/scripts/dev_port.py" "${API_PORT:-8000}")"
export API_PORT
echo "Development API: http://127.0.0.1:$API_PORT"

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM

  if [[ -n "${SERVER_PID:-}" ]]; then
    kill -TERM -- "-$SERVER_PID" 2>/dev/null || true
  fi

  if [[ -n "${FRONTEND_PID:-}" ]]; then
    kill -TERM -- "-$FRONTEND_PID" 2>/dev/null || true
  fi

  wait "${SERVER_PID:-}" 2>/dev/null || true
  wait "${FRONTEND_PID:-}" 2>/dev/null || true

  exit "$exit_code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Explicit assignment also overrides a port inherited through MAKEFLAGS.
make server API_PORT="$API_PORT" &
SERVER_PID=$!

(
  cd frontend
  "$NPM" run dev
) &
FRONTEND_PID=$!

wait -n "$SERVER_PID" "$FRONTEND_PID"

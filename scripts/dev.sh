#!/usr/bin/env bash
set -euo pipefail

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

cleanup() {
  local exit_code=$?

  if [[ -n "${SERVER_PID:-}" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
  fi

  if [[ -n "${FRONTEND_PID:-}" ]] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi

  wait "${SERVER_PID:-}" 2>/dev/null || true
  wait "${FRONTEND_PID:-}" 2>/dev/null || true

  exit "$exit_code"
}
trap cleanup EXIT INT TERM

make server &
SERVER_PID=$!

(
  cd frontend
  "$NPM" run dev
) &
FRONTEND_PID=$!

wait -n "$SERVER_PID" "$FRONTEND_PID"

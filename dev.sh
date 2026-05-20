#!/usr/bin/env bash
# Start Perspicacité for local development: the Python backend (REST API +
# MCP on :8000) and the Next.js frontend (web UI on :3000) together.
#
#   ./dev.sh
#
# A single Ctrl+C stops BOTH — the trap kills the whole process group, so you
# don't need the `fg`-then-Ctrl+C dance that `serve & (… npm run dev)` forces.
set -euo pipefail
cd "$(dirname "$0")"

# Kill every process in this script's group on exit (Ctrl+C, error, or normal).
trap 'trap - INT TERM EXIT; kill 0' INT TERM EXIT

uv run perspicacite -c config.yml serve &
(cd frontend && npm run dev) &

wait

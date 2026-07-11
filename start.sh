#!/usr/bin/env bash
#
# start.sh — set up the Python environment (if needed) and start the NAS Portal.
#
#   ./start.sh                 # listen on :8000
#   ./start.sh --port 9000     # listen on :9000
#   ./start.sh --help
#
# Creates a virtualenv at .venv and installs requirements.txt the first time
# (or whenever requirements.txt changes), then runs the Flask server.

set -euo pipefail

PORT="${PORT:-8000}"
CONFIG_DIR=""

usage() {
  cat <<'EOF'
Usage: ./start.sh [OPTIONS]

Start the NAS Portal server. Creates a Python virtualenv at .venv (installing
dependencies from requirements.txt) if it isn't already present, then runs the
Flask server.

Options:
  -p, --port PORT      Port to listen on (default: 8000)
  -c, --config DIR     Config directory for JSON state (default: ./config)
  -h, --help           Show this help and exit

Environment variables (options take precedence):
  PORT                 Port to listen on (default: 8000)
  NASPORTAL_CONFIG     Config directory (default: ./config)
  NASPORTAL_SECURE_COOKIE  Set to "1" to mark the session cookie Secure (HTTPS)
  FLASK_DEBUG          Set to "1" to enable debug / auto-reload mode

Examples:
  ./start.sh
  ./start.sh --port 9000
  ./start.sh -p 8080 --config /etc/nasportal
  FLASK_DEBUG=1 ./start.sh
EOF
}

# --- parse args (supports both --port 9000 and --port=9000) ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--port)   PORT="$2"; shift 2 ;;
    --port=*)    PORT="${1#*=}"; shift ;;
    -c|--config) CONFIG_DIR="$2"; shift 2 ;;
    --config=*)  CONFIG_DIR="${1#*=}"; shift ;;
    -h|--help)   usage; exit 0 ;;
    *) echo "start.sh: unknown option: $1" >&2; echo >&2; usage >&2; exit 1 ;;
  esac
done

# Always run relative to this script's location, regardless of cwd.
cd "$(dirname "$0")"

VENV=".venv"
STAMP="$VENV/.req_stamp"
NEW_STAMP=$(sha256sum requirements.txt 2>/dev/null | cut -d' ' -f1 || true)
NEED_INSTALL=0

# Create the virtualenv if it's missing or broken.
if [[ ! -x "$VENV/bin/python" ]]; then
  echo ">> Creating virtualenv at $VENV ..."
  python3 -m venv "$VENV"
  NEED_INSTALL=1
fi

# (Re)install if it's a fresh venv or requirements.txt has changed.
if [[ ! -f "$STAMP" || "$(cat "$STAMP" 2>/dev/null || true)" != "$NEW_STAMP" ]]; then
  NEED_INSTALL=1
fi

if [[ $NEED_INSTALL -eq 1 ]]; then
  echo ">> Installing dependencies from requirements.txt ..."
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r requirements.txt
  echo "$NEW_STAMP" > "$STAMP"
fi

export PORT
[[ -n "$CONFIG_DIR" ]] && export NASPORTAL_CONFIG="$CONFIG_DIR"

CONFIG_DISPLAY="${NASPORTAL_CONFIG:-./config}"
echo ">> Starting NAS Portal on http://0.0.0.0:${PORT} (config: ${CONFIG_DISPLAY})"
exec "$VENV/bin/python" backend/app.py
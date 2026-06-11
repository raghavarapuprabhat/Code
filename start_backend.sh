#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
ENV_FILE="$SCRIPT_DIR/.env"

# Ensure .env exists
if [ ! -f "$ENV_FILE" ]; then
  if [ -f "$SCRIPT_DIR/.env.example" ]; then
    echo "No .env found — copying from .env.example. Edit $ENV_FILE before continuing."
    cp "$SCRIPT_DIR/.env.example" "$ENV_FILE"
    exit 1
  else
    echo "Error: $ENV_FILE not found. Create it with at least ANTHROPIC_API_KEY set." >&2
    exit 1
  fi
fi

# Create venv if missing
VENV_DIR="$BACKEND_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtual environment..."
  python3 -m venv "$VENV_DIR"
fi

# Activate venv
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

# Install/sync dependencies
echo "Installing dependencies..."
pip install -q -e "$BACKEND_DIR"

# Load .env into the current shell
set -o allexport
# shellcheck source=/dev/null
source "$ENV_FILE"
set +o allexport

echo "Starting backend on http://localhost:8000 ..."
cd "$BACKEND_DIR"
uvicorn app.main:app --reload --port 8000

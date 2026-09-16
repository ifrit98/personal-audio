#!/usr/bin/env bash
#
# Run the Discord bot front end for digest.sh.
#
# Creates .venv on first run and installs discord.py into it, so the system
# Python stays untouched. Everything else in this repo is stdlib-only.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/.env"
    set +a
fi

if [[ ! -x "$VENV/bin/python" ]]; then
    echo "Creating virtualenv at $VENV ..."
    python3 -m venv "$VENV"
fi

# Install or update deps only when requirements.txt is newer than the stamp.
STAMP="$VENV/.requirements-stamp"
if [[ ! -f "$STAMP" || "$SCRIPT_DIR/requirements.txt" -nt "$STAMP" ]]; then
    echo "Installing dependencies ..."
    "$VENV/bin/python" -m pip install --quiet --upgrade pip
    "$VENV/bin/python" -m pip install --quiet -r "$SCRIPT_DIR/requirements.txt"
    touch "$STAMP"
fi

if [[ -z "${DISCORD_BOT_TOKEN:-}" ]]; then
    echo "Error: DISCORD_BOT_TOKEN is not set." >&2
    echo "       Add it to .env — see the Discord Bot section of the README." >&2
    exit 1
fi

exec "$VENV/bin/python" "$SCRIPT_DIR/discord_bot.py" "$@"

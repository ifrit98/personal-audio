#!/usr/bin/env bash
#
# Playlist -> transcripts -> analytical reports.
#
# Thin wrapper around digest.py: loads .env, checks dependencies, and hands the
# arguments straight through. Run with --help for the full option list.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/.env"
    set +a
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 not found on PATH." >&2
    exit 1
fi

if ! command -v yt-dlp >/dev/null 2>&1; then
    echo "Warning: yt-dlp not found; only local files will work." >&2
    echo "         Install it with: brew install yt-dlp" >&2
fi

if [[ ! -x "$SCRIPT_DIR/whisper.cpp/build/bin/whisper-cli" ]]; then
    echo "Warning: whisper.cpp is not built; transcription will fail." >&2
    echo "         Build it with: ./setup.sh" >&2
fi

exec python3 "$SCRIPT_DIR/digest.py" "$@"

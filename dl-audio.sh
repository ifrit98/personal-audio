#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTDIR="$SCRIPT_DIR/downloads"
FORMAT="mp3"
QUALITY="0"  # 0 = best for mp3 (VBR ~245kbps), 5 = good, 9 = worst
SLEEP_MIN=3
SLEEP_MAX=10
RATE_LIMIT="100K"
SAFE_MODE=false

usage() {
    cat <<'USAGE'
Usage: dl-audio.sh [OPTIONS] URL [URL...]

Download and extract audio from YouTube videos.

Options:
  -f, --format FMT     Audio format: mp3, opus, flac, wav, m4a (default: mp3)
  -q, --quality Q      Audio quality 0-9, 0=best (default: 0)
  -o, --output DIR     Output directory (default: ~/Projects/Audio/downloads)
  -l, --list           List available formats for the URL (no download)
  -s, --safe           Enable rate-limiting to avoid IP bans
  -h, --help           Show this help

Examples:
  dl-audio.sh "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  dl-audio.sh -f flac "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  dl-audio.sh -s "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  dl-audio.sh -l "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
USAGE
    exit 0
}

LIST_ONLY=false
URLS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -f|--format)   FORMAT="$2"; shift 2 ;;
        -q|--quality)  QUALITY="$2"; shift 2 ;;
        -o|--output)   OUTDIR="$2"; shift 2 ;;
        -l|--list)     LIST_ONLY=true; shift ;;
        -s|--safe)     SAFE_MODE=true; shift ;;
        -h|--help)     usage ;;
        -*)            echo "Unknown option: $1" >&2; exit 1 ;;
        *)             URLS+=("$1"); shift ;;
    esac
done

if [[ ${#URLS[@]} -eq 0 ]]; then
    echo "Error: no URL provided." >&2
    echo "Run with --help for usage." >&2
    exit 1
fi

for url in "${URLS[@]}"; do
    if $LIST_ONLY; then
        echo "=== Available formats for: $url ==="
        yt-dlp -F "$url"
        continue
    fi

    mkdir -p "$OUTDIR"

    CMD=(
        yt-dlp
        -x
        --audio-format "$FORMAT"
        --audio-quality "$QUALITY"
        -o "$OUTDIR/%(title)s.%(ext)s"
        --no-playlist
        --embed-thumbnail
        --add-metadata
    )

    if $SAFE_MODE; then
        CMD+=(
            --sleep-interval "$SLEEP_MIN"
            --max-sleep-interval "$SLEEP_MAX"
            --throttled-rate "$RATE_LIMIT"
        )
    fi

    CMD+=("$url")

    echo "Downloading audio from: $url"
    echo "  Format: $FORMAT | Quality: $QUALITY | Output: $OUTDIR"
    "${CMD[@]}"
    echo "Done: $url"
    echo
done

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WHISPER_BIN="$SCRIPT_DIR/whisper.cpp/build/bin/whisper-cli"
WHISPER_MODELS="$SCRIPT_DIR/whisper.cpp/models"
WHISPER_LIBS="$SCRIPT_DIR/whisper.cpp/build/src:$SCRIPT_DIR/whisper.cpp/build/ggml/src:$SCRIPT_DIR/whisper.cpp/build/ggml/src/ggml-metal:$SCRIPT_DIR/whisper.cpp/build/ggml/src/ggml-blas"

OUTDIR="$SCRIPT_DIR/downloads"
TRANSCRIPT_DIR="$SCRIPT_DIR/transcripts"
MODEL="base.en"
OUTPUT_FMT="txt"
AUDIO_FMT="mp3"
AUDIO_QUALITY="0"
SAFE_MODE=false
SKIP_DOWNLOAD=false
LANGUAGE="en"
THREADS=4

usage() {
    cat <<'USAGE'
Usage: transcribe.sh [OPTIONS] <URL | FILE> [URL | FILE ...]

Download YouTube audio and transcribe it with whisper.cpp in one step.
You can also pass local audio files directly (mp3, wav, flac, ogg).

Options:
  -m, --model MODEL      Whisper model: tiny.en, base.en, small.en, large-v3-turbo
                         (default: base.en)
  -o, --output DIR       Transcript output directory (default: ./transcripts)
  -O, --output-format F  Output format: txt, srt, vtt, json, csv, lrc (default: txt)
  -f, --audio-format F   Audio format for download: mp3, wav, flac (default: mp3)
  -l, --language LANG    Spoken language, or 'auto' to detect (default: en)
  -t, --threads N        CPU threads for whisper (default: 4)
  -s, --safe             Enable rate-limiting for downloads
      --no-download      Skip download, treat arguments as local file paths
  -h, --help             Show this help

Available models (in whisper.cpp/models/):
  base.en              Fast, good for clear English speech (~40s for 30min)
  small.en             Better accuracy, slower
  large-v3-turbo       Best accuracy, multilingual, slowest

Examples:
  transcribe.sh "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  transcribe.sh -m small.en "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  transcribe.sh -O srt "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  transcribe.sh --no-download my-recording.wav
  transcribe.sh -m large-v3-turbo -l auto "https://youtube.com/watch?v=..."
USAGE
    exit 0
}

INPUTS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--model)          MODEL="$2"; shift 2 ;;
        -o|--output)         TRANSCRIPT_DIR="$2"; shift 2 ;;
        -O|--output-format)  OUTPUT_FMT="$2"; shift 2 ;;
        -f|--audio-format)   AUDIO_FMT="$2"; shift 2 ;;
        -l|--language)       LANGUAGE="$2"; shift 2 ;;
        -t|--threads)        THREADS="$2"; shift 2 ;;
        -s|--safe)           SAFE_MODE=true; shift ;;
        --no-download)       SKIP_DOWNLOAD=true; shift ;;
        -h|--help)           usage ;;
        -*)                  echo "Unknown option: $1" >&2; exit 1 ;;
        *)                   INPUTS+=("$1"); shift ;;
    esac
done

if [[ ${#INPUTS[@]} -eq 0 ]]; then
    echo "Error: no URL or file provided." >&2
    echo "Run with --help for usage." >&2
    exit 1
fi

MODEL_PATH="$WHISPER_MODELS/ggml-${MODEL}.bin"
if [[ ! -f "$MODEL_PATH" ]]; then
    echo "Error: model not found at $MODEL_PATH" >&2
    echo "Available models:" >&2
    ls "$WHISPER_MODELS"/ggml-*.bin 2>/dev/null | sed 's/.*ggml-//;s/\.bin//' | sed 's/^/  /' >&2
    exit 1
fi

whisper_output_flag() {
    case "$1" in
        txt)  echo "--output-txt" ;;
        srt)  echo "--output-srt" ;;
        vtt)  echo "--output-vtt" ;;
        json) echo "--output-json" ;;
        csv)  echo "--output-csv" ;;
        lrc)  echo "--output-lrc" ;;
        *)    echo "Error: unsupported format '$1'" >&2; exit 1 ;;
    esac
}

download_audio() {
    local url="$1"
    mkdir -p "$OUTDIR"

    local cmd=(
        yt-dlp -x
        --audio-format "$AUDIO_FMT"
        --audio-quality "$AUDIO_QUALITY"
        -o "$OUTDIR/%(title)s.%(ext)s"
        --no-playlist
        --print after_move:filepath
    )

    if $SAFE_MODE; then
        cmd+=(--sleep-interval 3 --max-sleep-interval 10 --throttled-rate 100K)
    fi

    cmd+=("$url")
    "${cmd[@]}" | tail -1
}

run_whisper() {
    local audio_file="$1"
    local basename
    basename="$(basename "${audio_file%.*}")"

    mkdir -p "$TRANSCRIPT_DIR"

    local out_flag
    out_flag="$(whisper_output_flag "$OUTPUT_FMT")"

    echo "  Model: $MODEL | Language: $LANGUAGE | Threads: $THREADS"
    echo "  Output: $TRANSCRIPT_DIR/$basename.$OUTPUT_FMT"

    DYLD_LIBRARY_PATH="$WHISPER_LIBS" "$WHISPER_BIN" \
        -m "$MODEL_PATH" \
        -f "$audio_file" \
        -l "$LANGUAGE" \
        -t "$THREADS" \
        $out_flag \
        -of "$TRANSCRIPT_DIR/$basename" \
        -pp \
        -np
    echo
}

echo "============================================"
echo "  YouTube -> Audio -> Transcript Pipeline"
echo "============================================"
echo

for input in "${INPUTS[@]}"; do
    if $SKIP_DOWNLOAD; then
        if [[ ! -f "$input" ]]; then
            echo "Error: file not found: $input" >&2
            continue
        fi
        audio_file="$input"
        echo "[Transcribe] $audio_file"
    else
        echo "[Download] $input"
        audio_file="$(download_audio "$input")"
        if [[ -z "$audio_file" || ! -f "$audio_file" ]]; then
            echo "Error: download failed for $input" >&2
            continue
        fi
        echo "  Saved: $audio_file"
        echo "[Transcribe] $audio_file"
    fi

    run_whisper "$audio_file"
    echo "[Done] Transcript saved to: $TRANSCRIPT_DIR/"
    echo
done

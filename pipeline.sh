#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Flags routed to each stage
WHISPER_MODEL="base.en"
AUDIO_FMT="mp3"
LANGUAGE="en"
THREADS=4
SAFE_MODE=false

LLM_PROMPT=""
LLM_SYSTEM=""
LLM_PROMPT_FILE=""
LLM_MODEL=""
LLM_LOCAL=false
LLM_MAX_TOKENS=4096
LLM_TEMPERATURE="0.3"
OUTPUT_DIR="$SCRIPT_DIR/processed"

SKIP_TRANSCRIBE=false
SKIP_PROCESS=false

usage() {
    cat <<'USAGE'
Usage: pipeline.sh [OPTIONS] <URL | FILE> [URL | FILE ...]

Full pipeline: YouTube URL → audio → transcript → LLM processing.

Transcription options:
  -m, --model MODEL        Whisper model (default: base.en)
  -f, --audio-format FMT   Download format: mp3, wav, flac (default: mp3)
  -l, --language LANG      Spoken language or 'auto' (default: en)
  -t, --threads N          CPU threads for whisper (default: 4)
  -s, --safe               Rate-limit downloads

LLM processing options:
  -p, --prompt TEXT        Custom prompt for LLM (default: summarize)
  --system TEXT            Custom system prompt
  -P, --prompt-file FILE   Read prompt from a file
  --llm-model MODEL        LLM model name (default: gpt-4o / auto for local)
  --local                  Use LM Studio instead of OpenAI
  --max-tokens N           Max response tokens (default: 4096)
  --temperature N          Sampling temperature (default: 0.3)
  -o, --output DIR         Processed output directory (default: ./processed)

Pipeline control:
  --no-transcribe          Skip transcription, treat inputs as transcript files
  --no-process             Skip LLM processing (just download + transcribe)

  -h, --help               Show this help

Examples:
  pipeline.sh "https://www.youtube.com/watch?v=VIDEO_ID"
  pipeline.sh -p "List the 5 main arguments" "URL"
  pipeline.sh --local -m small.en "URL"
  pipeline.sh --no-process "URL"
  pipeline.sh --no-transcribe transcripts/video.txt
USAGE
    exit 0
}

INPUTS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--model)          WHISPER_MODEL="$2"; shift 2 ;;
        -f|--audio-format)   AUDIO_FMT="$2"; shift 2 ;;
        -l|--language)       LANGUAGE="$2"; shift 2 ;;
        -t|--threads)        THREADS="$2"; shift 2 ;;
        -s|--safe)           SAFE_MODE=true; shift ;;
        -p|--prompt)         LLM_PROMPT="$2"; shift 2 ;;
        --system)            LLM_SYSTEM="$2"; shift 2 ;;
        -P|--prompt-file)    LLM_PROMPT_FILE="$2"; shift 2 ;;
        --llm-model)         LLM_MODEL="$2"; shift 2 ;;
        --local)             LLM_LOCAL=true; shift ;;
        --max-tokens)        LLM_MAX_TOKENS="$2"; shift 2 ;;
        --temperature)       LLM_TEMPERATURE="$2"; shift 2 ;;
        -o|--output)         OUTPUT_DIR="$2"; shift 2 ;;
        --no-transcribe)     SKIP_TRANSCRIBE=true; shift ;;
        --no-process)        SKIP_PROCESS=true; shift ;;
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

echo "============================================"
echo "  Full Pipeline: URL → Audio → Transcript → LLM"
echo "============================================"
echo

TRANSCRIPT_FILES=()

for input in "${INPUTS[@]}"; do

    # ── Stage 1 & 2: Download + Transcribe ──────────────────────────────
    if $SKIP_TRANSCRIBE; then
        if [[ ! -f "$input" ]]; then
            echo "[Error] File not found: $input" >&2
            continue
        fi
        echo "[Skip Download+Transcribe] Using existing transcript: $input"
        TRANSCRIPT_FILES+=("$input")
    else
        echo "[Stage 1+2] Download + Transcribe: $input"

        transcribe_args=(
            -m "$WHISPER_MODEL"
            -f "$AUDIO_FMT"
            -l "$LANGUAGE"
            -t "$THREADS"
        )
        $SAFE_MODE && transcribe_args+=(-s)
        transcribe_args+=("$input")

        "$SCRIPT_DIR/transcribe.sh" "${transcribe_args[@]}"

        # Find the transcript that was just created (most recently modified .txt)
        latest_transcript=$(find "$SCRIPT_DIR/transcripts" -name "*.txt" -type f -newer "$SCRIPT_DIR/transcribe.sh" -print0 2>/dev/null \
            | xargs -0 ls -t 2>/dev/null | head -1)

        if [[ -z "$latest_transcript" ]]; then
            latest_transcript=$(ls -t "$SCRIPT_DIR/transcripts"/*.txt 2>/dev/null | head -1)
        fi

        if [[ -n "$latest_transcript" && -f "$latest_transcript" ]]; then
            TRANSCRIPT_FILES+=("$latest_transcript")
        else
            echo "[Error] No transcript produced for: $input" >&2
        fi
    fi
done

# ── Stage 3: LLM Processing ────────────────────────────────────────────

if $SKIP_PROCESS; then
    echo "[Skip LLM] --no-process flag set"
    echo
    echo "Transcripts available:"
    for t in "${TRANSCRIPT_FILES[@]}"; do
        echo "  $t"
    done
    exit 0
fi

if [[ ${#TRANSCRIPT_FILES[@]} -eq 0 ]]; then
    echo "[Error] No transcripts to process." >&2
    exit 1
fi

echo "[Stage 3] LLM Processing"
echo

process_args=(-o "$OUTPUT_DIR" --max-tokens "$LLM_MAX_TOKENS" --temperature "$LLM_TEMPERATURE")
[[ -n "$LLM_PROMPT" ]]      && process_args+=(-p "$LLM_PROMPT")
[[ -n "$LLM_SYSTEM" ]]      && process_args+=(--system "$LLM_SYSTEM")
[[ -n "$LLM_PROMPT_FILE" ]] && process_args+=(-P "$LLM_PROMPT_FILE")
[[ -n "$LLM_MODEL" ]]       && process_args+=(-m "$LLM_MODEL")
$LLM_LOCAL                   && process_args+=(--local)

process_args+=("${TRANSCRIPT_FILES[@]}")

"$SCRIPT_DIR/process.sh" "${process_args[@]}"

echo "============================================"
echo "  Pipeline complete."
echo "============================================"

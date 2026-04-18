#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Source .env if present (env vars take precedence)
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

OPENAI_API_KEY="${OPENAI_API_KEY:-}"
OPENAI_MODEL="${OPENAI_MODEL:-gpt-4o}"
LM_STUDIO_URL="${LM_STUDIO_URL:-http://localhost:1234/v1}"
LM_STUDIO_MODEL="${LM_STUDIO_MODEL:-}"

OUTPUT_DIR="$SCRIPT_DIR/processed"
USE_LOCAL=false
MODEL=""
MAX_TOKENS=4096
TEMPERATURE="0.3"
USER_PROMPT=""
SYSTEM_PROMPT=""
PROMPT_FILE=""
BATCH_DIR=""
USE_SUMMARIZE=false

DEFAULT_SYSTEM_PROMPT="You are an expert analyst. You receive transcripts of audio/video content. \
Respond to the user's request thoughtfully and thoroughly. Use markdown formatting. \
If the transcript is from a lecture or talk, preserve the speaker's key arguments and structure."

DEFAULT_USER_PROMPT="Summarize the key points from this transcript. \
Organize them with clear headings and bullet points."

SUMMARIZE_SYSTEM_PROMPT="You are an expert content analyst who distills audio and video transcripts \
into structured, actionable intelligence. You handle any genre: lectures, tutorials, how-to guides, \
interviews, philosophical discussions, debates, podcasts, sermons, conference talks, and more. \
Adapt your analysis to the nature of the content. Use markdown formatting throughout."

read -r -d '' SUMMARIZE_USER_PROMPT <<'PROMPT' || true
Analyze this transcript and produce a structured summary with the following sections:

## Overview
A 2-3 sentence description of what this content is (lecture, tutorial, conversation, etc.), who the speaker(s) are if identifiable, and the central theme.

## Key Takeaways
The most important ideas, arguments, or insights presented. Use bullet points. Capture the substance, not just surface-level observations. For philosophical or abstract content, distill the core thesis and supporting reasoning.

## Action Items & Practical Advice
Concrete steps, recommendations, or techniques the audience can apply. If the content is philosophical or theoretical rather than practical, reframe the key ideas as actionable principles or mental models. If there are truly no actionable elements, note that briefly and skip this section.

## Notable Quotes
3-5 of the most striking or representative quotes from the transcript, presented as blockquotes.

## Implications & Connections
Broader significance of the ideas presented. What fields, debates, or decisions do they connect to? What are the downstream consequences if the speaker's arguments are taken seriously?

## Critical Assessment
A brief, balanced evaluation: What is the strongest part of the argument? Where are the gaps, assumptions, or areas that deserve further scrutiny?
PROMPT

usage() {
    cat <<'USAGE'
Usage: process.sh [OPTIONS] <FILE> [FILE ...]
       process.sh [OPTIONS] -b <DIRECTORY>

Send transcripts to an LLM (OpenAI or local LM Studio) for processing.

Options:
  --summarize            Deep structured summary: takeaways, action items, quotes,
                         implications, and critical assessment
  -p, --prompt TEXT      Custom prompt for processing (default: basic summarize)
  -s, --system TEXT      Custom system prompt
  -P, --prompt-file FILE Read user prompt from a file
  -m, --model MODEL      Model name (default: gpt-4o / auto for local)
  --local                Use LM Studio instead of OpenAI
  -o, --output DIR       Output directory (default: ./processed)
  -b, --batch DIR        Process all .txt files in a directory
  --max-tokens N         Max response tokens (default: 4096)
  --temperature N        Sampling temperature 0.0-2.0 (default: 0.3)
  -h, --help             Show this help

Configuration:
  Set OPENAI_API_KEY via environment variable or .env file.
  Set LM_STUDIO_URL for custom local endpoint (default: http://localhost:1234/v1).

Examples:
  process.sh --summarize transcripts/video.txt
  process.sh -p "Extract all actionable advice as a bullet list" transcripts/video.txt
  process.sh --summarize -b transcripts/
  process.sh -P prompts/my-prompt.txt transcripts/video.txt
  process.sh --local --summarize transcripts/video.txt
USAGE
    exit 0
}

INPUTS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --summarize)       USE_SUMMARIZE=true; shift ;;
        -p|--prompt)       USER_PROMPT="$2"; shift 2 ;;
        -s|--system)       SYSTEM_PROMPT="$2"; shift 2 ;;
        -P|--prompt-file)  PROMPT_FILE="$2"; shift 2 ;;
        -m|--model)        MODEL="$2"; shift 2 ;;
        --local)           USE_LOCAL=true; shift ;;
        -o|--output)       OUTPUT_DIR="$2"; shift 2 ;;
        -b|--batch)        BATCH_DIR="$2"; shift 2 ;;
        --max-tokens)      MAX_TOKENS="$2"; shift 2 ;;
        --temperature)     TEMPERATURE="$2"; shift 2 ;;
        -h|--help)         usage ;;
        -*)                echo "Unknown option: $1" >&2; exit 1 ;;
        *)                 INPUTS+=("$1"); shift ;;
    esac
done

# Resolve prompt (--summarize sets the structured prompt; explicit -p/-P overrides it)
if [[ -n "$PROMPT_FILE" ]]; then
    if [[ ! -f "$PROMPT_FILE" ]]; then
        echo "Error: prompt file not found: $PROMPT_FILE" >&2
        exit 1
    fi
    USER_PROMPT="$(cat "$PROMPT_FILE")"
fi

if $USE_SUMMARIZE && [[ -z "$USER_PROMPT" ]]; then
    USER_PROMPT="$SUMMARIZE_USER_PROMPT"
    [[ -z "$SYSTEM_PROMPT" ]] && SYSTEM_PROMPT="$SUMMARIZE_SYSTEM_PROMPT"
fi

[[ -z "$USER_PROMPT" ]] && USER_PROMPT="$DEFAULT_USER_PROMPT"
[[ -z "$SYSTEM_PROMPT" ]] && SYSTEM_PROMPT="$DEFAULT_SYSTEM_PROMPT"

# Resolve batch mode
if [[ -n "$BATCH_DIR" ]]; then
    if [[ ! -d "$BATCH_DIR" ]]; then
        echo "Error: directory not found: $BATCH_DIR" >&2
        exit 1
    fi
    while IFS= read -r -d '' f; do
        INPUTS+=("$f")
    done < <(find "$BATCH_DIR" -maxdepth 1 -name "*.txt" -type f -print0 | sort -z)
fi

if [[ ${#INPUTS[@]} -eq 0 ]]; then
    echo "Error: no transcript file or batch directory provided." >&2
    echo "Run with --help for usage." >&2
    exit 1
fi

# Resolve API endpoint and model
if $USE_LOCAL; then
    API_BASE="$LM_STUDIO_URL"
    API_KEY="lm-studio"
    if [[ -n "$MODEL" ]]; then
        API_MODEL="$MODEL"
    elif [[ -n "$LM_STUDIO_MODEL" ]]; then
        API_MODEL="$LM_STUDIO_MODEL"
    else
        # Query LM Studio for loaded model
        loaded=$(curl -s "$API_BASE/models" 2>/dev/null | \
            python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data'][0]['id'])" 2>/dev/null) || true
        if [[ -n "$loaded" ]]; then
            API_MODEL="$loaded"
        else
            echo "Error: no model specified and couldn't auto-detect from LM Studio." >&2
            echo "Start a model in LM Studio or pass -m MODEL." >&2
            exit 1
        fi
    fi
else
    API_BASE="https://api.openai.com/v1"
    API_KEY="$OPENAI_API_KEY"
    API_MODEL="${MODEL:-$OPENAI_MODEL}"
    if [[ -z "$API_KEY" ]]; then
        echo "Error: OPENAI_API_KEY not set." >&2
        echo "Set it in .env or as an environment variable, or use --local for LM Studio." >&2
        exit 1
    fi
fi

json_escape() {
    python3 -c "import json,sys; print(json.dumps(sys.stdin.read()), end='')"
}

call_llm() {
    local transcript_text="$1"

    local full_user_msg="${USER_PROMPT}

--- TRANSCRIPT ---
${transcript_text}
--- END TRANSCRIPT ---"

    local system_escaped
    local user_escaped
    system_escaped=$(printf '%s' "$SYSTEM_PROMPT" | json_escape)
    user_escaped=$(printf '%s' "$full_user_msg" | json_escape)

    local payload
    payload=$(cat <<ENDJSON
{
  "model": "${API_MODEL}",
  "messages": [
    {"role": "system", "content": ${system_escaped}},
    {"role": "user", "content": ${user_escaped}}
  ],
  "max_tokens": ${MAX_TOKENS},
  "temperature": ${TEMPERATURE}
}
ENDJSON
)

    local response
    response=$(curl -s -w "\n%{http_code}" \
        "$API_BASE/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $API_KEY" \
        -d "$payload")

    local http_code
    http_code=$(echo "$response" | tail -1)
    local body
    body=$(echo "$response" | sed '$d')

    if [[ "$http_code" -ne 200 ]]; then
        echo "Error: API returned HTTP $http_code" >&2
        echo "$body" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    print(d.get('error',{}).get('message','Unknown error'), file=sys.stderr)
except: print(sys.stdin.read(), file=sys.stderr)
" 2>&2 || true
        return 1
    fi

    echo "$body" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(d['choices'][0]['message']['content'])
"
}

mkdir -p "$OUTPUT_DIR"

echo "============================================"
echo "  Transcript → LLM Processing Pipeline"
echo "============================================"
echo
echo "  Backend: $(if $USE_LOCAL; then echo "LM Studio ($API_BASE)"; else echo "OpenAI API"; fi)"
echo "  Model:   $API_MODEL"
echo "  Prompt:  ${USER_PROMPT:0:80}$(if [[ ${#USER_PROMPT} -gt 80 ]]; then echo '...'; fi)"
echo

for input_file in "${INPUTS[@]}"; do
    if [[ ! -f "$input_file" ]]; then
        echo "[Skip] File not found: $input_file" >&2
        continue
    fi

    basename="$(basename "${input_file%.*}")"
    out_file="$OUTPUT_DIR/$basename.md"

    echo "[Process] $input_file"
    echo "  → $out_file"

    transcript_text="$(cat "$input_file")"
    if [[ -z "$transcript_text" ]]; then
        echo "  [Skip] Empty file" >&2
        continue
    fi

    result=$(call_llm "$transcript_text") || {
        echo "  [Error] Failed to process $input_file" >&2
        continue
    }

    {
        echo "# $basename"
        echo
        echo "_Processed with ${API_MODEL} on $(date '+%Y-%m-%d %H:%M')_"
        echo
        echo "---"
        echo
        echo "$result"
    } > "$out_file"

    word_count=$(echo "$result" | wc -w | xargs)
    echo "  [Done] $word_count words"
    echo
done

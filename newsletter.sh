#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Source .env if present
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

OPENAI_API_KEY="${OPENAI_API_KEY:-}"
OPENAI_MODEL="${OPENAI_MODEL:-gpt-4o}"
LM_STUDIO_URL="${LM_STUDIO_URL:-http://localhost:1234/v1}"
LM_STUDIO_API_KEY="${LM_STUDIO_API_KEY:-${LM_STUDIO_API_TOKEN:-lm-studio}}"
LM_STUDIO_MODEL="${LM_STUDIO_MODEL:-}"

WHISPER_MODEL="base.en"
AUDIO_FMT="mp3"
LANGUAGE="en"
THREADS=4
SAFE_MODE=false
USE_LOCAL=false
LLM_MODEL=""
MAX_TOKENS=4096
TEMPERATURE="0.3"
NEWSLETTER_TITLE=""
REPORT_DIR=""
SKIP_TRANSCRIBE=false

usage() {
    cat <<'USAGE'
Usage: newsletter.sh [OPTIONS] <URL|FILE> [URL|FILE ...]
       newsletter.sh [OPTIONS] --from-file <url-list.txt>

Generate a newsletter-style digest from multiple YouTube videos (or existing
transcripts). Produces individual deep-dive reports for each video plus a
cross-video executive digest that surfaces the most important information.

Output structure:
  reports/<title>/
    ├── digest.md              # Cross-video executive summary
    ├── individual/
    │   ├── Video One.md       # Deep-dive report per video
    │   ├── Video Two.md
    │   └── ...
    └── transcripts/           # Raw transcripts (symlinked)

Options:
  --title TEXT             Newsletter title (default: auto-generated from date)
  --from-file FILE         Read URLs/paths from a file (one per line)
  -o, --output DIR         Report output directory (default: ./reports)

Transcription:
  -m, --model MODEL        Whisper model (default: base.en)
  -l, --language LANG      Spoken language or 'auto' (default: en)
  -t, --threads N          CPU threads for whisper (default: 4)
  -s, --safe               Rate-limit downloads
  --no-transcribe          Treat inputs as transcript files (skip download)

LLM:
  --local                  Use LM Studio instead of OpenAI
  --llm-model MODEL        LLM model name (default: gpt-4o / auto for local)
  --max-tokens N           Max response tokens (default: 4096)
  --temperature N          Sampling temperature (default: 0.3)

  -h, --help               Show this help

Examples:
  newsletter.sh "URL1" "URL2" "URL3"
  newsletter.sh --local --title "Weekly Research" "URL1" "URL2"
  newsletter.sh --from-file urls.txt
  newsletter.sh --no-transcribe transcripts/*.txt
USAGE
    exit 0
}

INPUTS=()
URL_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --title)           NEWSLETTER_TITLE="$2"; shift 2 ;;
        --from-file)       URL_FILE="$2"; shift 2 ;;
        -o|--output)       REPORT_DIR="$2"; shift 2 ;;
        -m|--model)        WHISPER_MODEL="$2"; shift 2 ;;
        -l|--language)     LANGUAGE="$2"; shift 2 ;;
        -t|--threads)      THREADS="$2"; shift 2 ;;
        -s|--safe)         SAFE_MODE=true; shift ;;
        --no-transcribe)   SKIP_TRANSCRIBE=true; shift ;;
        --local)           USE_LOCAL=true; shift ;;
        --llm-model)       LLM_MODEL="$2"; shift 2 ;;
        --max-tokens)      MAX_TOKENS="$2"; shift 2 ;;
        --temperature)     TEMPERATURE="$2"; shift 2 ;;
        -h|--help)         usage ;;
        -*)                echo "Unknown option: $1" >&2; exit 1 ;;
        *)                 INPUTS+=("$1"); shift ;;
    esac
done

if [[ -n "$URL_FILE" ]]; then
    if [[ ! -f "$URL_FILE" ]]; then
        echo "Error: URL file not found: $URL_FILE" >&2
        exit 1
    fi
    while IFS= read -r line; do
        line="${line%%#*}"    # strip comments
        line="${line// /}"    # strip whitespace
        [[ -n "$line" ]] && INPUTS+=("$line")
    done < "$URL_FILE"
fi

if [[ ${#INPUTS[@]} -eq 0 ]]; then
    echo "Error: no URLs or transcript files provided." >&2
    echo "Run with --help for usage." >&2
    exit 1
fi

DATE_STAMP="$(date '+%Y-%m-%d')"
[[ -z "$NEWSLETTER_TITLE" ]] && NEWSLETTER_TITLE="Digest $DATE_STAMP"
SAFE_TITLE="$(echo "$NEWSLETTER_TITLE" | tr '/:*?"<>|' '-' | tr ' ' '-')"
[[ -z "$REPORT_DIR" ]] && REPORT_DIR="$SCRIPT_DIR/reports"
NEWSLETTER_DIR="$REPORT_DIR/$SAFE_TITLE"

mkdir -p "$NEWSLETTER_DIR/individual" "$NEWSLETTER_DIR/transcripts"

# ── Resolve LLM backend ────────────────────────────────────────────────

if $USE_LOCAL; then
    API_BASE="$LM_STUDIO_URL"
    API_KEY="$LM_STUDIO_API_KEY"
    if [[ -n "$LLM_MODEL" ]]; then
        API_MODEL="$LLM_MODEL"
    elif [[ -n "$LM_STUDIO_MODEL" ]]; then
        API_MODEL="$LM_STUDIO_MODEL"
    else
        loaded=$(curl -s "$API_BASE/models" 2>/dev/null | \
            python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data'][0]['id'])" 2>/dev/null) || true
        if [[ -n "$loaded" ]]; then
            API_MODEL="$loaded"
        else
            echo "Error: no LLM model specified and couldn't auto-detect." >&2; exit 1
        fi
    fi
else
    API_BASE="https://api.openai.com/v1"
    API_KEY="$OPENAI_API_KEY"
    API_MODEL="${LLM_MODEL:-$OPENAI_MODEL}"
    if [[ -z "$API_KEY" ]]; then
        echo "Error: OPENAI_API_KEY not set. Use --local for LM Studio." >&2; exit 1
    fi
fi

json_escape() {
    python3 -c "import json,sys; print(json.dumps(sys.stdin.read()), end='')"
}

call_llm() {
    local system_prompt="$1"
    local user_prompt="$2"

    local sys_escaped usr_escaped
    sys_escaped=$(printf '%s' "$system_prompt" | json_escape)
    usr_escaped=$(printf '%s' "$user_prompt" | json_escape)

    local payload
    payload=$(cat <<ENDJSON
{
  "model": "${API_MODEL}",
  "messages": [
    {"role": "system", "content": ${sys_escaped}},
    {"role": "user", "content": ${usr_escaped}}
  ],
  "max_tokens": ${MAX_TOKENS},
  "temperature": ${TEMPERATURE}
}
ENDJSON
)

    local response http_code body
    response=$(curl -s -w "\n%{http_code}" \
        "$API_BASE/chat/completions" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $API_KEY" \
        -d "$payload")

    http_code=$(echo "$response" | tail -1)
    body=$(echo "$response" | sed '$d')

    if [[ "$http_code" -ne 200 ]]; then
        echo "Error: API returned HTTP $http_code" >&2
        echo "$body" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin); print(d.get('error',{}).get('message','Unknown error'), file=sys.stderr)
except: pass
" 2>&1 >&2 || true
        return 1
    fi

    echo "$body" | python3 -c "
import sys,json
d=json.load(sys.stdin)
msg=d['choices'][0]['message']
content=msg.get('content','') or ''
if not content.strip():
    content=msg.get('reasoning_content','') or ''
print(content)
"
}

# ── Prompts ─────────────────────────────────────────────────────────────

INDIVIDUAL_SYSTEM="You are an expert content analyst producing detailed reports from video/audio transcripts. \
Adapt your analysis to the content type (lecture, interview, tutorial, philosophical talk, news, etc.). \
Use markdown formatting throughout. Be thorough and substantive."

read -r -d '' INDIVIDUAL_PROMPT <<'PROMPT' || true
Produce a comprehensive deep-dive report on this transcript with the following sections:

## Overview
What this content is, who the speaker(s) are, and the central theme (2-3 sentences).

## Detailed Summary
A thorough walkthrough of the content's main arguments and ideas in the order presented. Use subheadings to break up major topic shifts. This should be detailed enough that someone who hasn't watched the video understands the full substance.

## Key Takeaways
The 5-10 most important insights, arguments, or findings as bullet points.

## Action Items & Practical Advice
Concrete steps or recommendations. For theoretical content, reframe as actionable principles or mental models.

## Notable Quotes
5-8 of the most striking quotes as blockquotes.

## Implications & Connections
How these ideas connect to broader fields, debates, or current events.

## Critical Assessment
Strengths, gaps, assumptions, and areas deserving further scrutiny.
PROMPT

DIGEST_SYSTEM="You are an expert editor producing a newsletter-style executive digest that synthesizes \
multiple video/audio analyses into a single cohesive briefing. You surface the most important information \
across all sources, identify cross-cutting themes, and help the reader decide which deep-dive reports \
to read in full. Use markdown formatting. Be concise but substantive."

read -r -d '' DIGEST_PROMPT_TEMPLATE <<'PROMPT' || true
You are producing an executive digest newsletter titled "TITLE_PLACEHOLDER" covering NVIDEOS_PLACEHOLDER videos.

Below are the individual deep-dive reports for each video. Synthesize them into a single newsletter with these sections:

## Top-Line Summary
A 3-5 sentence executive summary of the most important information across all videos. What should the reader know right now?

## Cross-Cutting Themes
Identify 3-5 themes or ideas that appear across multiple videos. For each theme, note which videos contribute to it and what the key insight is.

## Video Highlights
For each video, provide:
- **Title** (as a heading)
- A 2-3 sentence summary capturing the essence
- 1-2 "must-know" bullet points
- A note on who should read the full report (e.g., "Read if you're interested in X")

## Actionable Intelligence
The most practical, immediately useful information across all videos consolidated into a single list.

## Connections & Contradictions
Where do the videos agree, disagree, or complement each other? Any surprising connections?

## Reading Priority
Rank the individual reports by importance/urgency with a one-line justification for each.

---

Here are the individual reports:

REPORTS_PLACEHOLDER
PROMPT

# ── Main flow ──────────────────────────────────────────────────────────

echo "============================================"
echo "  Newsletter Generator"
echo "============================================"
echo
echo "  Title:    $NEWSLETTER_TITLE"
echo "  Videos:   ${#INPUTS[@]}"
echo "  Backend:  $(if $USE_LOCAL; then echo "LM Studio ($API_BASE)"; else echo "OpenAI API"; fi)"
echo "  Model:    $API_MODEL"
echo "  Output:   $NEWSLETTER_DIR/"
echo

# ── Stage 1: Transcribe ────────────────────────────────────────────────

TRANSCRIPT_FILES=()

for input in "${INPUTS[@]}"; do
    if $SKIP_TRANSCRIBE; then
        if [[ ! -f "$input" ]]; then
            echo "[Skip] File not found: $input" >&2
            continue
        fi
        TRANSCRIPT_FILES+=("$input")
        echo "[Transcript] $input"
    else
        echo "[Download+Transcribe] $input"
        transcribe_args=(-m "$WHISPER_MODEL" -f "$AUDIO_FMT" -l "$LANGUAGE" -t "$THREADS")
        $SAFE_MODE && transcribe_args+=(-s)
        transcribe_args+=("$input")

        "$SCRIPT_DIR/transcribe.sh" "${transcribe_args[@]}"

        latest=$(find "$SCRIPT_DIR/transcripts" -name "*.txt" -type f -newer "$SCRIPT_DIR/transcribe.sh" -print0 2>/dev/null \
            | xargs -0 ls -t 2>/dev/null | head -1)
        [[ -z "$latest" ]] && latest=$(ls -t "$SCRIPT_DIR/transcripts"/*.txt 2>/dev/null | head -1)

        if [[ -n "$latest" && -f "$latest" ]]; then
            TRANSCRIPT_FILES+=("$latest")
            echo "  → $latest"
        else
            echo "  [Error] No transcript produced" >&2
        fi
    fi
done

if [[ ${#TRANSCRIPT_FILES[@]} -eq 0 ]]; then
    echo "Error: no transcripts available." >&2
    exit 1
fi

# Symlink transcripts into the report directory
for t in "${TRANSCRIPT_FILES[@]}"; do
    ln -sf "$(cd "$(dirname "$t")" && pwd)/$(basename "$t")" "$NEWSLETTER_DIR/transcripts/" 2>/dev/null || true
done

echo
echo "── Stage 2: Individual Deep-Dive Reports ──"
echo

INDIVIDUAL_REPORTS=()
REPORT_NAMES=()

for transcript in "${TRANSCRIPT_FILES[@]}"; do
    title="$(basename "${transcript%.*}")"
    out_file="$NEWSLETTER_DIR/individual/$title.md"
    REPORT_NAMES+=("$title")

    echo "[Report] $title"
    echo "  → $out_file"

    transcript_text="$(cat "$transcript")"
    if [[ -z "$transcript_text" ]]; then
        echo "  [Skip] Empty transcript" >&2
        continue
    fi

    user_msg="$INDIVIDUAL_PROMPT

--- TRANSCRIPT ---
$transcript_text
--- END TRANSCRIPT ---"

    result=$(call_llm "$INDIVIDUAL_SYSTEM" "$user_msg") || {
        echo "  [Error] LLM failed for $title" >&2
        continue
    }

    {
        echo "# $title"
        echo
        echo "_Deep-dive report — ${API_MODEL} — $(date '+%Y-%m-%d %H:%M')_"
        echo
        echo "---"
        echo
        echo "$result"
    } > "$out_file"

    INDIVIDUAL_REPORTS+=("$result")
    word_count=$(echo "$result" | wc -w | xargs)
    echo "  [Done] $word_count words"
    echo
done

if [[ ${#INDIVIDUAL_REPORTS[@]} -eq 0 ]]; then
    echo "Error: no individual reports generated." >&2
    exit 1
fi

# ── Stage 3: Cross-Video Digest ────────────────────────────────────────

echo "── Stage 3: Cross-Video Digest ──"
echo

# Build the combined reports block
COMBINED=""
for i in "${!INDIVIDUAL_REPORTS[@]}"; do
    COMBINED+="### Report $((i+1)): ${REPORT_NAMES[$i]}

${INDIVIDUAL_REPORTS[$i]}

---

"
done

DIGEST_PROMPT="${DIGEST_PROMPT_TEMPLATE//TITLE_PLACEHOLDER/$NEWSLETTER_TITLE}"
DIGEST_PROMPT="${DIGEST_PROMPT//NVIDEOS_PLACEHOLDER/${#INDIVIDUAL_REPORTS[@]}}"
DIGEST_PROMPT="${DIGEST_PROMPT//REPORTS_PLACEHOLDER/$COMBINED}"

DIGEST_FILE="$NEWSLETTER_DIR/digest.md"
echo "[Digest] Synthesizing ${#INDIVIDUAL_REPORTS[@]} reports..."
echo "  → $DIGEST_FILE"

digest_result=$(call_llm "$DIGEST_SYSTEM" "$DIGEST_PROMPT") || {
    echo "[Error] Failed to generate digest" >&2
    exit 1
}

{
    echo "# $NEWSLETTER_TITLE"
    echo
    echo "_Newsletter digest — ${API_MODEL} — $(date '+%Y-%m-%d %H:%M')_"
    echo
    echo "_${#INDIVIDUAL_REPORTS[@]} videos analyzed. Individual deep-dive reports in \`individual/\`._"
    echo
    echo "---"
    echo
    echo "$digest_result"
    echo
    echo "---"
    echo
    echo "## Individual Reports"
    echo
    for name in "${REPORT_NAMES[@]}"; do
        echo "- [$name](individual/$name.md)"
    done
} > "$DIGEST_FILE"

digest_words=$(echo "$digest_result" | wc -w | xargs)
echo "  [Done] $digest_words words"

echo
echo "============================================"
echo "  Newsletter complete!"
echo "============================================"
echo
echo "  Digest:  $DIGEST_FILE"
echo "  Reports: $NEWSLETTER_DIR/individual/"
echo "  Videos:  ${#INDIVIDUAL_REPORTS[@]}"
echo

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

TEST_URL="https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo" — 19 seconds, first YouTube video
PASS=0
FAIL=0
TOTAL=0

info()    { printf "\033[1;34m==> %s\033[0m\n" "$*"; }
pass()    { printf "\033[1;32m  PASS: %s\033[0m\n" "$*"; PASS=$((PASS+1)); TOTAL=$((TOTAL+1)); }
fail()    { printf "\033[1;31m  FAIL: %s\033[0m\n" "$*"; FAIL=$((FAIL+1)); TOTAL=$((TOTAL+1)); }
section() { echo; printf "\033[1;37m── %s ──\033[0m\n" "$*"; }

cleanup() {
    rm -rf "$SCRIPT_DIR/test_output"
}
trap cleanup EXIT

mkdir -p test_output

# ── Check prerequisites ─────────────────────────────────────────────────

section "Prerequisites"

for cmd in yt-dlp ffmpeg ffprobe cmake; do
    if command -v "$cmd" &>/dev/null; then
        pass "$cmd found"
    else
        fail "$cmd not found"
    fi
done

WHISPER_BIN="$SCRIPT_DIR/whisper.cpp/build/bin/whisper-cli"
if [[ -x "$WHISPER_BIN" ]]; then
    pass "whisper-cli built"
else
    fail "whisper-cli not found at $WHISPER_BIN (run ./setup.sh first)"
fi

MODEL_PATH="$SCRIPT_DIR/whisper.cpp/models/ggml-base.en.bin"
if [[ -f "$MODEL_PATH" ]]; then
    pass "base.en model present"
else
    fail "base.en model not found (run ./setup.sh first)"
fi

if [[ -f .gitmodules ]]; then
    pass ".gitmodules exists"
else
    fail ".gitmodules missing"
fi

# ── Test audio download ─────────────────────────────────────────────────

section "Audio Download (dl-audio.sh)"

info "Downloading test video: $TEST_URL"
dl_output=$(./dl-audio.sh -o test_output/audio "$TEST_URL" 2>&1) || true

audio_file=$(find test_output/audio -name "*.mp3" -type f 2>/dev/null | head -1)
if [[ -n "$audio_file" && -f "$audio_file" ]]; then
    size=$(stat -f%z "$audio_file" 2>/dev/null || stat -c%s "$audio_file" 2>/dev/null)
    if (( size > 1000 )); then
        pass "Audio downloaded ($(du -h "$audio_file" | cut -f1 | xargs))"
    else
        fail "Audio file too small ($size bytes)"
    fi
else
    fail "No audio file produced"
    echo "$dl_output"
fi

# ── Test transcription on local file ────────────────────────────────────

section "Transcription (transcribe.sh --no-download)"

if [[ -n "$audio_file" && -f "$audio_file" ]]; then
    info "Transcribing local file: $audio_file"
    ./transcribe.sh --no-download -o test_output/transcripts "$audio_file" 2>&1 || true

    transcript=$(find test_output/transcripts -name "*.txt" -type f 2>/dev/null | head -1)
    if [[ -n "$transcript" && -f "$transcript" ]]; then
        word_count=$(wc -w < "$transcript" | xargs)
        if (( word_count > 5 )); then
            pass "Transcript generated ($word_count words)"
            echo "  Preview: $(head -c 200 "$transcript")..."
        else
            fail "Transcript too short ($word_count words)"
        fi
    else
        fail "No transcript file produced"
    fi
else
    fail "Skipped — no audio file from previous step"
fi

# ── Test full pipeline ──────────────────────────────────────────────────

section "Full Pipeline (transcribe.sh with URL)"

info "Running full pipeline: $TEST_URL"
rm -rf test_output/pipeline_audio test_output/pipeline_transcripts
mkdir -p test_output/pipeline_audio test_output/pipeline_transcripts

OUTDIR="$SCRIPT_DIR/test_output/pipeline_audio" \
    ./transcribe.sh -o test_output/pipeline_transcripts "$TEST_URL" 2>&1 || true

pipeline_transcript=$(find test_output/pipeline_transcripts -name "*.txt" -type f 2>/dev/null | head -1)
if [[ -n "$pipeline_transcript" && -f "$pipeline_transcript" ]]; then
    word_count=$(wc -w < "$pipeline_transcript" | xargs)
    if (( word_count > 5 )); then
        pass "Full pipeline produced transcript ($word_count words)"
    else
        fail "Pipeline transcript too short ($word_count words)"
    fi
else
    fail "Full pipeline produced no transcript"
fi

# ── Summary ─────────────────────────────────────────────────────────────

echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if (( FAIL == 0 )); then
    printf "\033[1;32m  All %d tests passed.\033[0m\n" "$TOTAL"
else
    printf "\033[1;31m  %d/%d tests failed.\033[0m\n" "$FAIL" "$TOTAL"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

exit "$FAIL"

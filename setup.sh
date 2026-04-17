#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

DEFAULT_MODEL="base.en"

info()  { printf "\033[1;34m==> %s\033[0m\n" "$*"; }
ok()    { printf "\033[1;32m  ✓ %s\033[0m\n" "$*"; }
warn()  { printf "\033[1;33m  ! %s\033[0m\n" "$*"; }
fail()  { printf "\033[1;31m  ✗ %s\033[0m\n" "$*"; exit 1; }

usage() {
    cat <<'USAGE'
Usage: setup.sh [OPTIONS]

Set up the full audio pipeline: install dependencies, build whisper.cpp,
and download a whisper model.

Options:
  -m, --model MODEL    Whisper model to download (default: base.en)
                       Choices: tiny, tiny.en, base, base.en, small, small.en,
                                medium, medium.en, large-v3-turbo
  --all-models         Download base.en, small.en, and large-v3-turbo
  --skip-brew          Skip Homebrew dependency installation
  --rebuild            Force rebuild whisper.cpp even if binary exists
  -h, --help           Show this help
USAGE
    exit 0
}

MODELS=()
SKIP_BREW=false
FORCE_REBUILD=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--model)      MODELS+=("$2"); shift 2 ;;
        --all-models)    MODELS=("base.en" "small.en" "large-v3-turbo"); shift ;;
        --skip-brew)     SKIP_BREW=true; shift ;;
        --rebuild)       FORCE_REBUILD=true; shift ;;
        -h|--help)       usage ;;
        -*)              echo "Unknown option: $1" >&2; exit 1 ;;
        *)               shift ;;
    esac
done

[[ ${#MODELS[@]} -eq 0 ]] && MODELS=("$DEFAULT_MODEL")

# ── 1. System dependencies ──────────────────────────────────────────────

info "Checking system dependencies..."

if ! $SKIP_BREW; then
    if ! command -v brew &>/dev/null; then
        fail "Homebrew not found. Install from https://brew.sh or run with --skip-brew"
    fi

    for pkg in yt-dlp ffmpeg cmake; do
        if command -v "$pkg" &>/dev/null; then
            ok "$pkg ($(command -v "$pkg"))"
        else
            info "Installing $pkg..."
            brew install "$pkg"
            ok "$pkg installed"
        fi
    done
else
    for pkg in yt-dlp ffmpeg cmake; do
        if command -v "$pkg" &>/dev/null; then
            ok "$pkg ($(command -v "$pkg"))"
        else
            fail "$pkg not found. Install it or run without --skip-brew"
        fi
    done
fi

# ── 2. Git submodules ───────────────────────────────────────────────────

info "Initializing git submodules..."

if [[ -f .gitmodules ]]; then
    git submodule update --init --recursive
    ok "whisper.cpp @ $(git -C whisper.cpp rev-parse --short HEAD)"
    ok "yt-dlp      @ $(git -C yt-dlp rev-parse --short HEAD)"
else
    fail ".gitmodules not found — are you in the repo root?"
fi

# ── 3. Build whisper.cpp ────────────────────────────────────────────────

WHISPER_BIN="whisper.cpp/build/bin/whisper-cli"

if [[ -x "$WHISPER_BIN" ]] && ! $FORCE_REBUILD; then
    ok "whisper-cli already built ($WHISPER_BIN)"
else
    info "Building whisper.cpp..."
    cd whisper.cpp
    cmake -B build
    cmake --build build --config Release -j"$(sysctl -n hw.ncpu)"
    cd "$SCRIPT_DIR"
    ok "whisper-cli built successfully"
fi

# ── 4. Download whisper models ──────────────────────────────────────────

info "Checking whisper models..."

for model in "${MODELS[@]}"; do
    model_file="whisper.cpp/models/ggml-${model}.bin"
    if [[ -f "$model_file" ]]; then
        ok "$model ($(du -h "$model_file" | cut -f1 | xargs))"
    else
        info "Downloading model: $model..."
        bash whisper.cpp/models/download-ggml-model.sh "$model"
        ok "$model downloaded"
    fi
done

# ── 5. Create output directories ────────────────────────────────────────

mkdir -p downloads transcripts processed

# ── 6. Environment config ───────────────────────────────────────────────

info "Checking environment config..."

if [[ -f .env ]]; then
    ok ".env already exists"
else
    if [[ -f .env.example ]]; then
        cp .env.example .env
        ok ".env created from .env.example (edit it to add your API keys)"
        warn "Run: \$EDITOR .env"
    else
        warn ".env.example not found — skipping .env creation"
    fi
fi

# ── 7. Summary ──────────────────────────────────────────────────────────

echo
info "Setup complete!"
echo
echo "  Quick start:"
echo "    ./transcribe.sh \"https://www.youtube.com/watch?v=VIDEO_ID\""
echo
echo "  Full pipeline (download + transcribe + LLM):"
echo "    ./pipeline.sh \"https://www.youtube.com/watch?v=VIDEO_ID\""
echo
echo "  Run the test suite:"
echo "    ./test.sh"
echo
echo "  Available models:"
for model in "${MODELS[@]}"; do
    echo "    - $model"
done
echo
echo "  Next step: edit .env to add your OPENAI_API_KEY (or use --local for LM Studio)"
echo

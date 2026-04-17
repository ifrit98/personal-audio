# Personal Audio Pipeline

A local-first pipeline for downloading YouTube audio, transcribing it with
[whisper.cpp](https://github.com/ggml-org/whisper.cpp), and processing
transcripts with LLMs (OpenAI API or local models via LM Studio).

## Prerequisites

- **macOS** (Apple Silicon or Intel)
- [Homebrew](https://brew.sh)
- Git
- An OpenAI API key **or** [LM Studio](https://lmstudio.ai) for local models

Everything else is installed automatically by `setup.sh`.

## Quick Start

```bash
# 1. Clone the repo (with submodules)
git clone --recurse-submodules https://github.com/ifrit98/personal-audio.git
cd personal-audio

# 2. Run setup (installs deps, builds whisper.cpp, downloads model, creates .env)
./setup.sh

# 3. Add your OpenAI API key
$EDITOR .env

# 4. Run the full pipeline on a YouTube video
./pipeline.sh "https://www.youtube.com/watch?v=VIDEO_ID"

# 5. (Optional) Verify everything works
./test.sh
```

If you already cloned without `--recurse-submodules`:

```bash
git submodule update --init --recursive
./setup.sh
```

## What Setup Does

`setup.sh` handles the full environment in one command:

1. **Installs system packages** via Homebrew: `yt-dlp`, `ffmpeg`, `cmake`
2. **Initializes git submodules**: pulls `whisper.cpp` and `yt-dlp` source
3. **Builds whisper.cpp** from source (uses Metal/GPU acceleration on Apple Silicon)
4. **Downloads a whisper model** (default: `base.en`, ~141 MB)
5. **Creates `.env`** from `.env.example` for API key configuration
6. **Creates output directories**: `downloads/`, `transcripts/`, `processed/`

### Setup Options

```bash
./setup.sh                     # Default: install everything, download base.en model
./setup.sh -m small.en         # Use a different model
./setup.sh --all-models        # Download base.en + small.en + large-v3-turbo
./setup.sh --skip-brew         # Skip Homebrew (if deps are already installed)
./setup.sh --rebuild           # Force rebuild whisper.cpp
```

## Usage

### Full Pipeline: URL to Processed Summary

The end-to-end command. Downloads audio, transcribes it, and sends the
transcript to an LLM:

```bash
./pipeline.sh "https://www.youtube.com/watch?v=VIDEO_ID"
```

With a custom prompt:

```bash
./pipeline.sh -p "Extract all actionable advice as a numbered list" "URL"
```

Using a local model instead of OpenAI:

```bash
./pipeline.sh --local "URL"
```

### Download + Transcribe Only (no LLM)

```bash
./pipeline.sh --no-process "URL"
# or equivalently:
./transcribe.sh "URL"
```

### Download Audio Only

```bash
./dl-audio.sh "https://www.youtube.com/watch?v=VIDEO_ID"
```

### Process an Existing Transcript

```bash
./process.sh transcripts/video.txt
./process.sh -p "Write a blog post based on this" transcripts/video.txt
./process.sh -b transcripts/   # batch process all transcripts
```

### Transcribe a Local Audio File

```bash
./transcribe.sh --no-download recording.mp3
```

### Multiple URLs

All scripts accept multiple inputs:

```bash
./pipeline.sh "URL1" "URL2" "URL3"
./transcribe.sh "URL1" "URL2" "URL3"
./dl-audio.sh "URL1" "URL2" "URL3"
```

## Options Reference

### pipeline.sh

| Flag | Description | Default |
|---|---|---|
| `-m, --model MODEL` | Whisper model for transcription | `base.en` |
| `-f, --audio-format FMT` | Download format: `mp3`, `wav`, `flac` | `mp3` |
| `-l, --language LANG` | Spoken language or `auto` | `en` |
| `-t, --threads N` | CPU threads for whisper | `4` |
| `-s, --safe` | Rate-limit downloads | off |
| `-p, --prompt TEXT` | Custom LLM prompt | summarize |
| `--system TEXT` | Custom LLM system prompt | analyst |
| `-P, --prompt-file FILE` | Read prompt from file | — |
| `--llm-model MODEL` | LLM model name | `gpt-4o` |
| `--local` | Use LM Studio instead of OpenAI | off |
| `--max-tokens N` | Max LLM response tokens | `4096` |
| `--temperature N` | LLM sampling temperature | `0.3` |
| `-o, --output DIR` | Processed output directory | `./processed` |
| `--no-transcribe` | Skip download+transcribe, use transcript files | off |
| `--no-process` | Skip LLM processing | off |

### process.sh

| Flag | Description | Default |
|---|---|---|
| `-p, --prompt TEXT` | Custom prompt | summarize |
| `-s, --system TEXT` | Custom system prompt | analyst |
| `-P, --prompt-file FILE` | Read prompt from file | — |
| `-m, --model MODEL` | Model name | `gpt-4o` / auto |
| `--local` | Use LM Studio | off |
| `-o, --output DIR` | Output directory | `./processed` |
| `-b, --batch DIR` | Process all `.txt` files in directory | — |
| `--max-tokens N` | Max response tokens | `4096` |
| `--temperature N` | Sampling temperature | `0.3` |

### transcribe.sh

| Flag | Description | Default |
|---|---|---|
| `-m, --model MODEL` | Whisper model (see table below) | `base.en` |
| `-O, --output-format FMT` | Transcript format: `txt`, `srt`, `vtt`, `json`, `csv`, `lrc` | `txt` |
| `-f, --audio-format FMT` | Audio download format: `mp3`, `wav`, `flac` | `mp3` |
| `-l, --language LANG` | Spoken language code, or `auto` for detection | `en` |
| `-t, --threads N` | CPU threads for whisper | `4` |
| `-o, --output DIR` | Transcript output directory | `./transcripts` |
| `-s, --safe` | Rate-limit downloads (avoids IP bans) | off |
| `--no-download` | Treat arguments as local file paths | off |

### dl-audio.sh

| Flag | Description | Default |
|---|---|---|
| `-f, --format FMT` | Audio format: `mp3`, `opus`, `flac`, `wav`, `m4a` | `mp3` |
| `-q, --quality Q` | Audio quality 0-9 (0 = best) | `0` |
| `-o, --output DIR` | Output directory | `./downloads` |
| `-l, --list` | List all available formats for a URL (no download) | — |
| `-s, --safe` | Rate-limit downloads (avoids IP bans) | off |

## LLM Configuration

### OpenAI API

Edit `.env` (created by `setup.sh`):

```bash
OPENAI_API_KEY=sk-your-key-here
OPENAI_MODEL=gpt-4o
```

Or set as environment variables:

```bash
OPENAI_API_KEY=sk-... ./process.sh transcripts/video.txt
```

### LM Studio (Local Models)

1. Install [LM Studio](https://lmstudio.ai)
2. Download and load a model in LM Studio
3. Start the local server (default: `http://localhost:1234/v1`)
4. Use the `--local` flag:

```bash
./process.sh --local transcripts/video.txt
./pipeline.sh --local "URL"
```

The script auto-detects the loaded model. To specify one explicitly:

```bash
./process.sh --local -m "llama-3-8b" transcripts/video.txt
```

Or set in `.env`:

```bash
LM_STUDIO_URL=http://localhost:1234/v1
LM_STUDIO_MODEL=llama-3-8b
```

### Custom Prompts

Inline:

```bash
./process.sh -p "Extract all actionable advice as a bullet list" transcripts/video.txt
```

From a file:

```bash
echo "Write a detailed blog post based on this transcript" > prompts/blog.txt
./process.sh -P prompts/blog.txt transcripts/video.txt
```

Custom system prompt:

```bash
./process.sh -s "You are a tech journalist writing for Hacker News" -p "Summarize this" transcripts/video.txt
```

## Whisper Models

Models are stored in `whisper.cpp/models/` and gitignored (download via `setup.sh`).

| Model | Size | Speed (30 min audio, M3) | Best For |
|---|---|---|---|
| `base.en` | 141 MB | ~40 sec | Fast transcription of clear English |
| `small.en` | 465 MB | ~2 min | Better accuracy, still fast |
| `large-v3-turbo` | 1.5 GB | ~5 min | Best accuracy, multilingual |

English-only models (`*.en`) are faster and more accurate for English content.
Use `large-v3-turbo` with `-l auto` for non-English or mixed-language audio.

Download additional models any time:

```bash
./setup.sh -m large-v3-turbo
# or manually:
bash whisper.cpp/models/download-ggml-model.sh small.en
```

## Output Formats

`transcribe.sh` supports multiple output formats via `-O`:

| Format | Flag | Description |
|---|---|---|
| Plain text | `-O txt` | Simple text transcript (default) |
| SRT subtitles | `-O srt` | SubRip format with timestamps |
| VTT subtitles | `-O vtt` | WebVTT format with timestamps |
| JSON | `-O json` | Structured data with timestamps |
| CSV | `-O csv` | Comma-separated values |
| LRC | `-O lrc` | Lyrics format with timestamps |

## Testing

`test.sh` validates the full pipeline end-to-end using a short test video
([Me at the zoo](https://www.youtube.com/watch?v=jNQXAC9IVRw) — 19 seconds):

```bash
./test.sh
```

It checks:
- All system dependencies are installed
- whisper-cli is built and the model is present
- Audio download works (`dl-audio.sh`)
- Local file transcription works (`transcribe.sh --no-download`)
- Full URL-to-transcript pipeline works (`transcribe.sh`)

Test artifacts are cleaned up automatically on exit.

## Avoiding IP Bans

For bulk downloads, use the `--safe` / `-s` flag to throttle requests:

```bash
./pipeline.sh -s "URL1" "URL2" "URL3"
./dl-audio.sh -s "URL1" "URL2" "URL3"
```

This adds sleep intervals between requests and limits download speed.
For heavy usage, consider rotating cookies:

```bash
yt-dlp --cookies-from-browser chrome -x "URL"
```

## Project Structure

```
personal-audio/
├── pipeline.sh        # Full pipeline: URL → audio → transcript → LLM
├── transcribe.sh      # Download + transcribe (no LLM)
├── dl-audio.sh        # Audio-only downloader
├── process.sh         # LLM transcript processor
├── setup.sh           # One-command setup: deps, build, model, .env
├── test.sh            # End-to-end test suite
├── .env.example       # Template for API keys (committed)
├── .env               # Your API keys (gitignored)
├── command.sh         # Legacy command reference
├── whisper.cpp/       # Git submodule — whisper.cpp source + models
│   ├── build/         # Compiled binaries (gitignored)
│   └── models/        # Model .bin files (gitignored)
├── yt-dlp/            # Git submodule — yt-dlp source
├── downloads/         # Downloaded audio files (gitignored)
├── transcripts/       # Generated transcripts (gitignored)
└── processed/         # LLM-processed output (gitignored)
```

## Troubleshooting

### whisper-cli crashes with "Library not loaded"

The build output is in `whisper.cpp/build/`. The scripts set `DYLD_LIBRARY_PATH`
automatically. If you see dylib errors, rebuild:

```bash
./setup.sh --rebuild
```

### yt-dlp fails with HTTP 403 or throttling

YouTube occasionally blocks IPs. Try:

```bash
# Update yt-dlp to latest
brew upgrade yt-dlp

# Use browser cookies for authentication
yt-dlp --cookies-from-browser chrome -x "URL"
```

### Model not found

Download it:

```bash
./setup.sh -m base.en
```

### Build fails (missing cmake, etc.)

```bash
./setup.sh   # Installs cmake and other deps via Homebrew
```

### LLM processing fails

- **OpenAI**: Check your `OPENAI_API_KEY` in `.env`
- **LM Studio**: Make sure the server is running and a model is loaded
- Test connectivity: `curl http://localhost:1234/v1/models`

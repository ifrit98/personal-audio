# Personal Audio Pipeline

A local-first pipeline for downloading YouTube audio and transcribing it with
[whisper.cpp](https://github.com/ggml-org/whisper.cpp). Everything runs on your
machine — no API keys, no cloud services, no data leaving your computer.

## Prerequisites

- **macOS** (Apple Silicon or Intel)
- [Homebrew](https://brew.sh)
- Git

Everything else is installed automatically by `setup.sh`.

## Quick Start

```bash
# 1. Clone the repo (with submodules)
git clone --recurse-submodules https://github.com/ifrit98/personal-audio.git
cd personal-audio

# 2. Run setup (installs deps, builds whisper.cpp, downloads the base.en model)
./setup.sh

# 3. Transcribe a YouTube video
./transcribe.sh "https://www.youtube.com/watch?v=VIDEO_ID"

# 4. (Optional) Verify everything works
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
5. **Creates output directories**: `downloads/` and `transcripts/`

### Setup Options

```bash
./setup.sh                     # Default: install everything, download base.en model
./setup.sh -m small.en         # Use a different model
./setup.sh --all-models        # Download base.en + small.en + large-v3-turbo
./setup.sh --skip-brew         # Skip Homebrew (if deps are already installed)
./setup.sh --rebuild           # Force rebuild whisper.cpp
```

## Usage

### Full Pipeline: Download + Transcribe

The main script. Give it a YouTube URL and get a transcript:

```bash
./transcribe.sh "https://www.youtube.com/watch?v=VIDEO_ID"
```

Output goes to `transcripts/<video-title>.txt`.

### Download Audio Only

Just extract the audio without transcribing:

```bash
./dl-audio.sh "https://www.youtube.com/watch?v=VIDEO_ID"
```

Output goes to `downloads/<video-title>.mp3`.

### Transcribe a Local File

Already have an audio file? Skip the download:

```bash
./transcribe.sh --no-download recording.mp3
```

### Multiple URLs

Both scripts accept multiple URLs:

```bash
./transcribe.sh "URL1" "URL2" "URL3"
./dl-audio.sh "URL1" "URL2" "URL3"
```

## Options Reference

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
| `-h, --help` | Show help | — |

### dl-audio.sh

| Flag | Description | Default |
|---|---|---|
| `-f, --format FMT` | Audio format: `mp3`, `opus`, `flac`, `wav`, `m4a` | `mp3` |
| `-q, --quality Q` | Audio quality 0–9 (0 = best) | `0` |
| `-o, --output DIR` | Output directory | `./downloads` |
| `-l, --list` | List all available formats for a URL (no download) | — |
| `-s, --safe` | Rate-limit downloads (avoids IP bans) | off |
| `-h, --help` | Show help | — |

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

```bash
# Generate subtitles
./transcribe.sh -O srt "https://www.youtube.com/watch?v=VIDEO_ID"

# Generate JSON with full metadata
./transcribe.sh -O json "https://www.youtube.com/watch?v=VIDEO_ID"
```

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
./transcribe.sh -s "URL1" "URL2" "URL3"
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
├── setup.sh           # One-command setup: deps, build, model download
├── transcribe.sh      # Full pipeline: YouTube URL → transcript
├── dl-audio.sh        # Audio-only downloader
├── test.sh            # End-to-end test suite
├── command.sh         # Legacy command reference
├── whisper.cpp/       # Git submodule — whisper.cpp source + models
│   ├── build/         # Compiled binaries (gitignored)
│   └── models/        # Model .bin files (gitignored)
├── yt-dlp/            # Git submodule — yt-dlp source
├── downloads/         # Downloaded audio files (gitignored)
└── transcripts/       # Generated transcripts (gitignored)
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

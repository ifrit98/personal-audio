# Personal Audio Pipeline

A local-first pipeline for downloading YouTube audio, transcribing it with
[whisper.cpp](https://github.com/ggml-org/whisper.cpp), and processing
transcripts with LLMs (OpenAI, Anthropic, OpenRouter, or local models via
LM Studio).

Three entry points do most of the work. `pipeline.sh` takes a single video from
URL to summary. `digest.sh` takes a whole playlist and turns each video into a
structured analytical report — themes, speaker-attributed claims, steel-manned
conclusions, supporting evidence, and a critical read — plus a cross-video
synthesis. `bot.sh` puts that behind a Discord bot so you can paste a playlist
link from your phone and get the reports back as files or a gist.

See [Playlist Digest](#playlist-digest-deep-analysis-at-scale) and
[Discord Bot](#discord-bot).

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

### Newsletter Digest: Multi-Video Summary

Generate a newsletter-style report from multiple videos with individual
deep-dive reports and a cross-video executive digest:

```bash
./newsletter.sh "URL1" "URL2" "URL3"
```

With a custom title and local model:

```bash
./newsletter.sh --local --title "Weekly Research" "URL1" "URL2" "URL3"
```

From a file of URLs (one per line, `#` comments allowed):

```bash
./newsletter.sh --from-file urls.txt
```

Using existing transcripts (skip download):

```bash
./newsletter.sh --no-transcribe transcripts/*.txt
```

Output structure:

```
reports/<title>/
├── digest.md              # Cross-video executive summary
├── individual/
│   ├── Video One.md       # Deep-dive report per video
│   ├── Video Two.md
│   └── ...
└── transcripts/           # Symlinked raw transcripts
```

### Playlist Digest: Deep Analysis at Scale

`digest.sh` is the "I saved 12 podcasts to a playlist and have no time to watch
them" path. Point it at a YouTube playlist (or a list file) and it downloads,
transcribes with timestamps, and turns each video into a structured report that
extracts the major themes, every claim attributed to the speaker who made it, a
steel-manned read of the conclusions, the evidence offered in support, and a
critical assessment. With more than one video it also writes a cross-video
`index.md` that surfaces consensus, live disagreements, and what you should
believe differently as a result.

```bash
./digest.sh "https://www.youtube.com/playlist?list=PLxxxxxxxx"
```

Check what you're in for before spending anything — this downloads nothing and
calls no provider:

```bash
./digest.sh --dry-run "https://www.youtube.com/playlist?list=PLxxxxxxxx"
```

Point it at a list file instead of a playlist. `txt`, `csv`, `json`, and `yaml`
all work:

```bash
./digest.sh --title "Macro Week 37" watchlist.csv
```

```csv
url,title,note
https://youtu.be/aaaaaaaaaaa,,Want the yen carry trade argument
https://youtu.be/bbbbbbbbbbb,,Steel-man the bear case
```

The optional `note` column is worth using: it tells the model why you saved the
video, and the analysis leans toward that interest without ignoring the rest.
Plain text lists use `URL | why I saved it`, and `#` starts a comment.

Other common runs:

```bash
# a specific provider and model
./digest.sh --provider anthropic --llm-model claude-sonnet-4-5 "URL"

# better transcription for hard audio, and only the first 5 of a long playlist
./digest.sh -m small.en --limit 5 "PLAYLIST_URL"

# analyze transcripts you already have
./digest.sh transcripts/*.txt

# fully local, no data leaves the machine
./digest.sh --provider lmstudio "PLAYLIST_URL"

# transcribe now, analyze later
./digest.sh --transcribe-only "PLAYLIST_URL"
```

Output structure:

```
digests/<title>/
├── index.md              # Start here: cross-video synthesis
├── videos/
│   ├── some-podcast.md   # Per-video report, timestamps deep-linked
│   └── ...
├── transcripts/          # .srt (timestamped) and .txt per video
├── notes/                # Intermediate chunk notes for long videos
└── state.json            # Resume ledger
```

Notes on behaviour worth knowing:

- **Resumable.** Re-running the same command skips videos that already have a
  report, so an interrupted 20-video run picks up where it stopped. Use
  `--force` to redo them anyway.
- **Long videos.** Transcripts that fit the model's context window go in one
  pass. Anything longer is analyzed chunk by chunk into dense intermediate
  notes, then synthesized, so a 3-hour panel survives even an 8k-context local
  model. `--chunk` forces this mode; it costs more calls but holds onto more
  per-speaker detail.
- **Quotes are checked, not trusted.** Every quote in a report is matched back
  against the transcript. Anything the model paraphrased into quotation marks is
  flagged inline, and the count lands in the report's `unverified_quotes` field.
- **Timestamps are links.** `[00:42:15]` in a report links straight to that
  moment in the video, so you can verify any claim in one click.
- **Audio is cached** in `downloads/` and reused across runs.

To change what the analysis asks for, dump the prompts and edit them:

```bash
./digest.sh --write-prompts     # writes prompts/*.md
```

`digest.sh` picks them up automatically from then on.

### Discord Bot

`bot.sh` puts `digest.sh` behind a Discord bot, so the whole loop is: save
videos to a playlist on your phone, paste the link into Discord, get the reports
back as files or a gist link.

```bash
./bot.sh
```

First run creates `.venv` and installs `discord.py` into it. Everything else in
this repo stays stdlib-only.

**One-time Discord setup**

1. Create an application at
   [discord.com/developers/applications](https://discord.com/developers/applications).
2. Under **Bot**, reset and copy the token into `DISCORD_BOT_TOKEN` in `.env`.
3. Under **Bot → Privileged Gateway Intents**, enable **Message Content
   Intent**. The bot cannot read your links without it.
4. Under **OAuth2 → URL Generator**, tick `bot` scope plus **Send Messages**,
   **Attach Files**, and **Read Message History**, then open the generated URL
   to invite it.
5. Turn on Discord's Developer Mode (Settings → Advanced) so you can
   right-click to copy your user ID, and put it in `DISCORD_ALLOWED_USERS`.

The bot **refuses to start without an allowlist**. A single message can queue
hours of transcription and paid API calls, so it will not take orders from
anyone who merely happens to see the channel. Set at least one of
`DISCORD_ALLOWED_USERS`, `DISCORD_ALLOWED_CHANNELS`, or
`DISCORD_ALLOWED_GUILDS`.

**Using it**

DM the bot, @-mention it, or post in a channel listed in
`DISCORD_ALLOWED_CHANNELS`. It picks up URLs anywhere in the message, and reads
an attached `.txt`/`.csv`/`.json`/`.yaml` list if you'd rather send a file.

```
https://youtube.com/playlist?list=PLxxxx limit=3 whisper=small.en as=gist
```

Options can go anywhere in the message:

| Option | Effect |
|---|---|
| `limit=5` | Cap the number of videos (also capped by `DIGEST_MAX_VIDEOS`) |
| `whisper=small.en` | Better transcription, slower |
| `llm=gpt-5.6-sol` | Use a stronger model for this run |
| `provider=anthropic` | `openai`, `anthropic`, `openrouter`, or `lmstudio` |
| `title="Macro Week 37"` | Name the run and its output folder |
| `as=gist` | `files`, `gist`, or `both` — overrides `DIGEST_DELIVERY` |
| `safe` | Rate-limit downloads |
| `force` | Redo videos already analyzed |
| `dryrun` | List what would be processed, costing nothing |

Say `help` to get this list in Discord.

**What it does**

Jobs run one at a time — whisper is CPU-bound, so parallel runs would only slow
each other down — and the bot replies with your queue position. It edits a
status message as transcription progresses, then delivers:

- **`files`**: `index.md` plus each per-video report as attachments. If there
  are more than 9 files or they exceed the upload limit, it sends a zip instead.
- **`gist`**: a secret gist (needs `GITHUB_TOKEN` with `gist` scope) and replies
  with the link. If gist creation fails it falls back to attachments rather than
  losing the work.

Either way it posts the roll-up's opening section inline, so the headline is
readable without opening anything, and warns you if any quotes failed
verification.

Reports also stay on disk under `digests/`, so a failed upload is never a lost
run — and since `digest.sh` is resumable, re-sending the same playlist picks up
where it stopped instead of paying twice.

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

### digest.sh

| Flag | Description | Default |
|---|---|---|
| `--title TEXT` | Run title, used for the output folder | playlist title or date |
| `-o, --output DIR` | Output root | `./digests` |
| `--limit N` | Process at most N videos | all |
| `--force` | Reprocess videos that already have a report | off |
| `--retranscribe` | Re-run whisper even if a transcript exists | off |
| `--single` | Treat a watch URL containing `&list=` as one video | off |
| `--dry-run` | Resolve the video list and estimate work, then stop | off |
| `--no-rollup` | Skip the cross-video synthesis | off |
| `-q, --quiet` | Only report errors | off |
| `-m, --model MODEL` | Whisper model | `base.en` |
| `-l, --language LANG` | Spoken language or `auto` | `en` |
| `-t, --threads N` | CPU threads for whisper | `4` |
| `-f, --audio-format FMT` | Download format: mp3, wav, flac, ogg | `mp3` |
| `-s, --safe` | Rate-limit downloads | off |
| `--transcribe-only` | Download and transcribe, skip all LLM analysis | off |
| `--provider NAME` | `openai`, `anthropic`, `openrouter`, or `lmstudio` | first configured key |
| `--llm-model MODEL` | Model name | `gpt-5.6-luna`, or `DIGEST_MODEL` |
| `--base-url URL` | Override the provider API base URL | — |
| `--max-tokens N` | Max response tokens | `8192` |
| `--temperature N` | Sampling temperature | `0.2` |
| `--context-tokens N` | Override the model's context window | auto-detected |
| `--chunk` | Always map-reduce, even when the transcript fits | auto |
| `--no-verify-quotes` | Skip checking quotes against the transcript | off |
| `--timeout SEC` | Per-request timeout | `300` |
| `--prompts-dir DIR` | Directory of prompt overrides | `./prompts` |
| `--write-prompts` | Write the default prompts to `--prompts-dir` and exit | — |

### newsletter.sh

| Flag | Description | Default |
|---|---|---|
| `--title TEXT` | Newsletter title | `Digest YYYY-MM-DD` |
| `--from-file FILE` | Read URLs/paths from a file (one per line) | — |
| `-o, --output DIR` | Report output directory | `./reports` |
| `-m, --model MODEL` | Whisper model for transcription | `base.en` |
| `-l, --language LANG` | Spoken language or `auto` | `en` |
| `-t, --threads N` | CPU threads for whisper | `4` |
| `-s, --safe` | Rate-limit downloads | off |
| `--no-transcribe` | Treat inputs as transcript files | off |
| `--local` | Use LM Studio instead of OpenAI | off |
| `--llm-model MODEL` | LLM model name | `gpt-4o` / auto |
| `--max-tokens N` | Max LLM response tokens | `4096` |
| `--temperature N` | LLM sampling temperature | `0.3` |

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

### Anthropic and OpenRouter (digest.sh only)

`pipeline.sh`, `process.sh`, and `newsletter.sh` speak OpenAI-compatible APIs
only. `digest.sh` additionally supports Anthropic's Messages API and OpenRouter:

```bash
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-5

OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=anthropic/claude-sonnet-4.5
```

Provider selection order is `--provider`, then `DIGEST_PROVIDER` in `.env`, then
whichever of `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` is set
first, then LM Studio. So with only an OpenAI key configured nothing changes; add
an Anthropic key and pick it per run with `--provider anthropic`.

### Model defaults

`digest.sh` defaults to **`gpt-5.6-luna`**: a 1.05M-token context window at
$0.20 / $1.20 per million input / output tokens. The large window matters here
because it means even a 3-hour panel is analyzed in a single pass rather than
chunked, which preserves per-speaker claim detail that map-reduce inevitably
blurs. Override it per run with `--llm-model`, or globally with `DIGEST_MODEL`
in `.env`.

`DIGEST_MODEL` applies to `digest.sh` only and takes precedence over
`OPENAI_MODEL`, so the older scripts can stay on a different model.

One cost note: GPT-5.6 and GPT-6 models switch to a long-context pricing tier
above 272K input tokens, roughly doubling the input rate. Nothing normal gets
close — a 3-hour podcast is about 45K tokens — but a roll-up across dozens of
videos could. `digest.sh` warns before it happens rather than letting you find
out on the invoice.

Context windows are inferred from the model name and can be overridden with
`--context-tokens` when a model reports something unusual — that number is what
decides whether a long transcript goes through in one pass or gets chunked.
Parameter quirks (`max_tokens` versus `max_completion_tokens`, whether
`temperature` is accepted) are learned from the API on the first call of a run
and reused, so new model releases work without a code change.

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
├── newsletter.sh      # Multi-video newsletter digest generator
├── digest.sh          # Playlist → deep analytical reports (wraps digest.py)
├── digest.py          # Playlist digest implementation (stdlib only)
├── bot.sh             # Discord bot launcher (creates .venv on first run)
├── discord_bot.py     # Discord front end for digest.py
├── requirements.txt   # discord.py — the only third-party dependency
├── prompts/           # Optional prompt overrides for digest.sh
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
├── processed/         # LLM-processed output (gitignored)
├── reports/           # Newsletter digests (gitignored)
└── digests/           # Playlist digests (gitignored)
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

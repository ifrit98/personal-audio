# Personal Audio Pipeline

Download YouTube audio and transcribe it locally with whisper.cpp.

## Setup

### 1. Install dependencies

```bash
brew install yt-dlp ffmpeg
```

### 2. Clone whisper.cpp and build

```bash
cd ~/Projects/Audio
git clone https://github.com/ggml-org/whisper.cpp.git
cd whisper.cpp
cmake -B build
cmake --build build --config Release
```

### 3. Download a whisper model

```bash
bash whisper.cpp/models/download-ggml-model.sh base.en
# Other options: small.en, large-v3-turbo
```

## Usage

### Full pipeline (download + transcribe)

```bash
./transcribe.sh "https://www.youtube.com/watch?v=VIDEO_ID"
```

### Just download audio

```bash
./dl-audio.sh "https://www.youtube.com/watch?v=VIDEO_ID"
```

### Transcribe a local file

```bash
./transcribe.sh --no-download recording.mp3
```

### Options (transcribe.sh)

| Flag | Description | Default |
|---|---|---|
| `-m, --model` | Whisper model (`base.en`, `small.en`, `large-v3-turbo`) | `base.en` |
| `-O, --output-format` | `txt`, `srt`, `vtt`, `json`, `csv`, `lrc` | `txt` |
| `-f, --audio-format` | Download format (`mp3`, `wav`, `flac`) | `mp3` |
| `-l, --language` | Language code or `auto` | `en` |
| `-t, --threads` | CPU threads | `4` |
| `-s, --safe` | Rate-limit downloads | off |

### Options (dl-audio.sh)

| Flag | Description | Default |
|---|---|---|
| `-f, --format` | Audio format (`mp3`, `opus`, `flac`, `wav`, `m4a`) | `mp3` |
| `-q, --quality` | Quality 0-9 (0 = best) | `0` |
| `-o, --output` | Output directory | `./downloads` |
| `-l, --list` | List available formats | - |
| `-s, --safe` | Rate-limit downloads | off |

## File structure

```
~/Projects/Audio/
├── transcribe.sh      # Full pipeline: URL → audio → transcript
├── dl-audio.sh        # Audio-only downloader
├── command.sh         # Legacy command reference
├── whisper.cpp/       # (cloned separately, not in repo)
├── yt-dlp/            # (cloned separately, not in repo)
├── downloads/         # Downloaded audio files (gitignored)
└── transcripts/       # Generated transcripts (gitignored)
```

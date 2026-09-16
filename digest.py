#!/usr/bin/env python3
"""YouTube playlist -> whisper.cpp transcripts -> structured analytical reports.

Takes a YouTube playlist (or a list of videos in a csv/json/yaml/txt file),
transcribes each video with whisper.cpp keeping timestamps, then sends each
transcript to an LLM to extract major themes, speaker-attributed claims, a
steel-manned read of the conclusions, the corroborating evidence offered, and a
critical assessment. Long transcripts are analyzed with a map-reduce pass so a
1.5-hour podcast survives a small context window without losing claim detail.

Stdlib only. Run via ./digest.sh, or directly with python3 digest.py --help.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent

WHISPER_BIN = SCRIPT_DIR / "whisper.cpp/build/bin/whisper-cli"
WHISPER_MODELS = SCRIPT_DIR / "whisper.cpp/models"
WHISPER_LIBS = ":".join(
    str(SCRIPT_DIR / p)
    for p in (
        "whisper.cpp/build/src",
        "whisper.cpp/build/ggml/src",
        "whisper.cpp/build/ggml/src/ggml-metal",
        "whisper.cpp/build/ggml/src/ggml-blas",
    )
)

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".opus", ".aac", ".wma"}
TRANSCRIPT_EXTS = {".srt", ".vtt", ".txt", ".md"}

# Rough char-per-token ratio for English prose. Deliberately conservative so
# chunks land under the real limit rather than over it.
CHARS_PER_TOKEN = 3.6


# ──────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────


class Log:
    quiet = False

    @staticmethod
    def say(msg: str = "") -> None:
        if not Log.quiet:
            print(msg, flush=True)

    @staticmethod
    def step(tag: str, msg: str) -> None:
        Log.say(f"[{tag}] {msg}")

    @staticmethod
    def detail(msg: str) -> None:
        Log.say(f"  {msg}")

    @staticmethod
    def warn(msg: str) -> None:
        print(f"  [warn] {msg}", file=sys.stderr, flush=True)

    @staticmethod
    def error(msg: str) -> None:
        print(f"[error] {msg}", file=sys.stderr, flush=True)


# ──────────────────────────────────────────────────────────────────────────
# Environment
# ──────────────────────────────────────────────────────────────────────────


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE pairs from a .env file without clobbering real env vars."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# ──────────────────────────────────────────────────────────────────────────
# Data types
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class VideoRef:
    """A video to process, plus whatever metadata we know about it so far."""

    url: str
    video_id: str | None = None
    title: str | None = None
    channel: str | None = None
    duration: int | None = None
    upload_date: str | None = None
    note: str | None = None
    local_audio: Path | None = None
    local_transcript: Path | None = None

    @property
    def key(self) -> str:
        return self.video_id or self.url

    @property
    def display(self) -> str:
        return self.title or self.video_id or self.url

    @property
    def watch_url(self) -> str:
        if self.video_id:
            return f"https://www.youtube.com/watch?v={self.video_id}"
        return self.url


@dataclass
class Segment:
    start: float
    end: float
    text: str

    @property
    def stamp(self) -> str:
        return format_timestamp(self.start)


@dataclass
class Transcript:
    segments: list[Segment]
    has_timestamps: bool
    source: Path | None = None

    @property
    def duration(self) -> float:
        return self.segments[-1].end if self.segments else 0.0


# ──────────────────────────────────────────────────────────────────────────
# Small helpers
# ──────────────────────────────────────────────────────────────────────────


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def format_duration(seconds: float | None) -> str:
    if not seconds:
        return "unknown"
    total = int(seconds)
    if total >= 3600:
        return f"{total // 3600}h{total % 3600 // 60:02d}m"
    return f"{total // 60}m{total % 60:02d}s"


def est_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def slugify(value: str, max_len: int = 90) -> str:
    """Filesystem-safe, readable slug. Keeps words, drops punctuation."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^\w\s-]", "", ascii_only).strip()
    slug = re.sub(r"[\s_-]+", "-", cleaned).strip("-")
    return (slug[:max_len].rstrip("-") or "untitled").lower()


def utc_stamp() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def run(cmd: Sequence[str], *, env: dict[str, str] | None = None,
        capture: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    merged_env = {**os.environ, **(env or {})}
    return subprocess.run(
        list(cmd),
        env=merged_env,
        check=check,
        text=True,
        capture_output=capture,
    )


# ──────────────────────────────────────────────────────────────────────────
# Input resolution: playlists, single videos, and list files
# ──────────────────────────────────────────────────────────────────────────

YOUTUBE_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})"
)
URL_IN_TEXT_RE = re.compile(r"https?://[^\s,;'\"<>)\]]+")
PLAYLIST_URL_RE = re.compile(
    r"[?&]list=|/playlist\b|youtube\.com/(?:@[^/]+|c/[^/]+|channel/[^/]+|user/[^/]+)"
)
URL_FIELD_NAMES = ("url", "link", "video", "video_url", "videourl", "href")
NOTE_FIELD_NAMES = ("note", "notes", "why", "context", "reason", "comment")
TITLE_FIELD_NAMES = ("title", "name", "video_title")


def extract_video_id(url: str) -> str | None:
    match = YOUTUBE_ID_RE.search(url)
    return match.group(1) if match else None


def looks_like_playlist(url: str) -> bool:
    return bool(PLAYLIST_URL_RE.search(url))


def expand_playlist(url: str, limit: int | None) -> tuple[list[VideoRef], str | None]:
    """Resolve a playlist/channel URL into video refs using a flat yt-dlp pass."""
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-single-json",
        "--ignore-errors",
        "--no-warnings",
    ]
    if limit:
        cmd += ["--playlist-end", str(limit)]
    cmd.append(url)

    Log.detail(f"resolving playlist: {url}")
    result = run(cmd, check=False)
    if result.returncode != 0 and not result.stdout.strip():
        raise RuntimeError(f"yt-dlp could not read playlist: {result.stderr.strip()}")

    payload = json.loads(result.stdout)
    if payload.get("_type") != "playlist":
        return [ref_from_entry(payload)], payload.get("title")

    refs: list[VideoRef] = []
    for entry in payload.get("entries") or []:
        if not entry:
            continue
        # Nested playlists (a channel's tabs, for instance) flatten one level.
        if entry.get("_type") == "playlist":
            refs.extend(
                ref_from_entry(sub) for sub in (entry.get("entries") or []) if sub
            )
        else:
            refs.append(ref_from_entry(entry))
    return refs, payload.get("title")


def ref_from_entry(entry: dict[str, Any]) -> VideoRef:
    video_id = entry.get("id")
    url = entry.get("url") or entry.get("webpage_url") or ""
    if video_id and (not url or not url.startswith("http")):
        url = f"https://www.youtube.com/watch?v={video_id}"
    duration = entry.get("duration")
    return VideoRef(
        url=url,
        video_id=video_id,
        title=entry.get("title"),
        channel=entry.get("channel") or entry.get("uploader"),
        duration=int(duration) if isinstance(duration, (int, float)) else None,
        upload_date=entry.get("upload_date"),
    )


def parse_yaml_list(text: str) -> tuple[list[dict[str, Any]], str | None]:
    """Parse the subset of YAML this tool needs, without requiring PyYAML.

    Handles a top-level list of URLs, a top-level list of mappings, and a
    mapping with a `videos:`/`urls:` list. Anything more exotic should install
    PyYAML, which is used automatically when present.
    """
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text)
        return normalize_loaded_list(loaded)
    except ImportError:
        pass

    items: list[dict[str, Any]] = []
    title: str | None = None
    current: dict[str, Any] | None = None

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip() if not raw.strip().startswith("#") else ""
        if not line.strip():
            continue
        stripped = line.strip()

        if stripped.startswith("- "):
            body = stripped[2:].strip()
            current = {}
            items.append(current)
            if ":" in body and not body.startswith(("http://", "https://")):
                key, _, value = body.partition(":")
                current[key.strip().lower()] = value.strip().strip("\"'")
            else:
                current["url"] = body.strip("\"'")
        elif ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip().lower()
            value = value.strip().strip("\"'")
            indented = line[: len(line) - len(line.lstrip())]
            if indented and current is not None:
                current[key] = value
            elif key == "title":
                title = value
            elif key in ("videos", "urls", "items", "entries"):
                current = None

    return [item for item in items if item.get("url")], title


def normalize_loaded_list(loaded: Any) -> tuple[list[dict[str, Any]], str | None]:
    """Coerce a parsed JSON/YAML document into (rows, title)."""
    title: str | None = None
    if isinstance(loaded, dict):
        title = loaded.get("title") or loaded.get("name")
        for key in ("videos", "urls", "items", "entries", "links"):
            if isinstance(loaded.get(key), list):
                loaded = loaded[key]
                break
        else:
            loaded = [loaded]

    if not isinstance(loaded, list):
        raise ValueError("expected a list of videos")

    rows: list[dict[str, Any]] = []
    for item in loaded:
        if isinstance(item, str):
            rows.append({"url": item})
        elif isinstance(item, dict):
            rows.append({str(k).lower(): v for k, v in item.items()})
    return rows, title


def pick_field(row: dict[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if row.get(name):
            return row[name]
    return None


def rows_to_refs(rows: Sequence[dict[str, Any]]) -> list[VideoRef]:
    refs: list[VideoRef] = []
    for row in rows:
        url = pick_field(row, URL_FIELD_NAMES)
        if not url:
            # Fall back to any value in the row that looks like a URL.
            for value in row.values():
                if isinstance(value, str) and URL_IN_TEXT_RE.match(value.strip()):
                    url = value.strip()
                    break
        if not url:
            continue
        url = str(url).strip()
        refs.append(
            VideoRef(
                url=url,
                video_id=extract_video_id(url),
                title=pick_field(row, TITLE_FIELD_NAMES),
                note=pick_field(row, NOTE_FIELD_NAMES),
            )
        )
    return refs


def load_list_file(path: Path) -> tuple[list[VideoRef], str | None]:
    text = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()

    if suffix == ".json":
        rows, title = normalize_loaded_list(json.loads(text))
        return rows_to_refs(rows), title

    if suffix in (".yaml", ".yml"):
        rows, title = parse_yaml_list(text)
        return rows_to_refs(rows), title

    if suffix in (".csv", ".tsv"):
        delimiter = "\t" if suffix == ".tsv" else ","
        lines = text.splitlines()
        # A header row names its columns; match whole cells so a URL containing
        # "watch" or "video" is never mistaken for one.
        first_cells = next(
            (
                [cell.strip().lower() for cell in row]
                for row in csv.reader(lines, delimiter=delimiter)
                if row and any(cell.strip() for cell in row)
            ),
            [],
        )
        known = set(URL_FIELD_NAMES + TITLE_FIELD_NAMES + NOTE_FIELD_NAMES)
        if first_cells and any(cell in known for cell in first_cells):
            reader = csv.DictReader(lines, delimiter=delimiter)
            rows = [
                {str(k).strip().lower(): v for k, v in row.items() if k}
                for row in reader
            ]
        else:
            rows = [
                {"url": cells[0], "note": cells[1] if len(cells) > 1 else None}
                for cells in csv.reader(lines, delimiter=delimiter)
                if cells and cells[0].strip()
            ]
        return rows_to_refs(rows), None

    # Plain text: one entry per line, `#` comments, optional "url | note".
    refs: list[VideoRef] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        url_part, _, note = line.partition("|")
        match = URL_IN_TEXT_RE.search(url_part)
        if not match:
            continue
        url = match.group(0)
        refs.append(
            VideoRef(
                url=url,
                video_id=extract_video_id(url),
                note=note.strip() or None,
            )
        )
    return refs, None


def attach_local(ref: VideoRef) -> VideoRef:
    """Flag a ref whose URL is really a path to local media or a transcript.

    Applies to refs from list files too, so a csv may mix URLs and local paths.
    """
    if ref.local_audio or ref.local_transcript or ref.url.startswith("http"):
        return ref
    candidate = Path(ref.url).expanduser()
    if not candidate.is_file():
        return ref
    suffix = candidate.suffix.lower()
    if suffix in AUDIO_EXTS:
        ref.local_audio = candidate.resolve()
    elif suffix in TRANSCRIPT_EXTS:
        ref.local_transcript = candidate.resolve()
    ref.title = ref.title or candidate.stem
    return ref


def resolve_inputs(inputs: Sequence[str], limit: int | None,
                   expand: bool) -> tuple[list[VideoRef], str | None]:
    """Turn CLI arguments into a de-duplicated list of videos to process."""
    refs: list[VideoRef] = []
    discovered_title: str | None = None

    for item in inputs:
        candidate = Path(item).expanduser()
        if candidate.is_file():
            suffix = candidate.suffix.lower()
            if suffix in AUDIO_EXTS or suffix in TRANSCRIPT_EXTS:
                refs.append(attach_local(VideoRef(url=str(candidate))))
                continue
            file_refs, title = load_list_file(candidate)
            if not file_refs:
                Log.warn(f"no videos found in {candidate}")
            refs.extend(file_refs)
            discovered_title = discovered_title or title
            continue

        if not item.startswith("http"):
            raise SystemExit(f"Not a URL or an existing file: {item}")

        if expand and looks_like_playlist(item):
            playlist_refs, title = expand_playlist(item, limit)
            refs.extend(playlist_refs)
            discovered_title = discovered_title or title
        else:
            refs.append(VideoRef(url=item, video_id=extract_video_id(item)))

    # A list file may itself contain local paths and playlist URLs; handle both.
    final: list[VideoRef] = []
    for ref in refs:
        ref = attach_local(ref)
        if expand and ref.local_audio is None and ref.local_transcript is None \
                and looks_like_playlist(ref.url) and not ref.video_id:
            playlist_refs, title = expand_playlist(ref.url, limit)
            for sub in playlist_refs:
                sub.note = sub.note or ref.note
            final.extend(playlist_refs)
            discovered_title = discovered_title or title
        else:
            final.append(ref)

    seen: set[str] = set()
    unique: list[VideoRef] = []
    for ref in final:
        if ref.key in seen:
            continue
        seen.add(ref.key)
        unique.append(ref)

    if limit:
        unique = unique[:limit]
    return unique, discovered_title


# ──────────────────────────────────────────────────────────────────────────
# Download + metadata
# ──────────────────────────────────────────────────────────────────────────


def find_existing_audio(download_dir: Path, video_id: str, audio_fmt: str) -> Path | None:
    if not download_dir.is_dir():
        return None
    marker = f"[{video_id}]"
    for path in download_dir.iterdir():
        if path.is_file() and marker in path.name and path.suffix.lower() == f".{audio_fmt}":
            return path
    return None


def read_info_json(audio_path: Path) -> dict[str, Any]:
    for candidate in (
        audio_path.with_suffix(".info.json"),
        audio_path.parent / f"{audio_path.stem}.info.json",
    ):
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8", errors="replace"))
            except (json.JSONDecodeError, OSError):
                return {}
    return {}


def download_audio(ref: VideoRef, download_dir: Path, audio_fmt: str,
                   safe: bool) -> Path:
    """Download and extract audio, reusing an existing file when present."""
    ensure_dir(download_dir)

    if ref.video_id:
        existing = find_existing_audio(download_dir, ref.video_id, audio_fmt)
        if existing:
            Log.detail(f"audio already downloaded: {existing.name}")
            apply_metadata(ref, read_info_json(existing))
            return existing

    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format", audio_fmt,
        "--audio-quality", "0",
        "-o", str(download_dir / "%(title)s [%(id)s].%(ext)s"),
        "--trim-filenames", "180",
        "--no-playlist",
        "--no-warnings",
        "--write-info-json",
        "--quiet",
        "--print", "after_move:filepath",
    ]
    if safe:
        cmd += ["--sleep-interval", "3", "--max-sleep-interval", "10",
                "--throttled-rate", "100K"]
    cmd.append(ref.url)

    result = run(cmd, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise RuntimeError(detail[-1] if detail else "yt-dlp failed")

    audio_path: Path | None = None
    for line in reversed(result.stdout.splitlines()):
        candidate = Path(line.strip())
        if line.strip() and candidate.is_file():
            audio_path = candidate
            break
    if audio_path is None and ref.video_id:
        audio_path = find_existing_audio(download_dir, ref.video_id, audio_fmt)
    if audio_path is None:
        raise RuntimeError("download succeeded but the audio file could not be located")

    apply_metadata(ref, read_info_json(audio_path))
    return audio_path


def fetch_metadata(ref: VideoRef) -> None:
    """Populate metadata without downloading media."""
    result = run(
        ["yt-dlp", "--dump-json", "--skip-download", "--no-warnings",
         "--no-playlist", ref.url],
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        try:
            apply_metadata(ref, json.loads(result.stdout.splitlines()[0]))
        except json.JSONDecodeError:
            pass


def apply_metadata(ref: VideoRef, info: dict[str, Any]) -> None:
    if not info:
        return
    ref.video_id = info.get("id") or ref.video_id
    ref.title = info.get("title") or ref.title
    ref.channel = info.get("channel") or info.get("uploader") or ref.channel
    ref.upload_date = info.get("upload_date") or ref.upload_date
    duration = info.get("duration")
    if isinstance(duration, (int, float)):
        ref.duration = int(duration)


# ──────────────────────────────────────────────────────────────────────────
# Transcription
# ──────────────────────────────────────────────────────────────────────────

SRT_TIME_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)
VTT_TIME_RE = SRT_TIME_RE


def transcribe(audio: Path, out_prefix: Path, whisper_model: str, language: str,
               threads: int, force: bool) -> Path:
    """Run whisper.cpp and return the path to the generated .srt file."""
    srt_path = out_prefix.with_suffix(".srt")
    if srt_path.is_file() and srt_path.stat().st_size > 0 and not force:
        Log.detail(f"transcript already exists: {srt_path.name}")
        return srt_path

    model_path = WHISPER_MODELS / f"ggml-{whisper_model}.bin"
    if not model_path.is_file():
        available = sorted(
            p.name.removeprefix("ggml-").removesuffix(".bin")
            for p in WHISPER_MODELS.glob("ggml-*.bin")
        )
        raise RuntimeError(
            f"whisper model not found: {model_path}\n"
            f"  available: {', '.join(available) or 'none (run ./setup.sh)'}"
        )

    ensure_dir(out_prefix.parent)
    Log.detail(
        f"whisper: model={whisper_model} language={language} threads={threads}"
    )
    result = run(
        [
            str(WHISPER_BIN),
            "-m", str(model_path),
            "-f", str(audio),
            "-l", language,
            "-t", str(threads),
            "--output-srt",
            "-of", str(out_prefix),
            "-np",
        ],
        env={"DYLD_LIBRARY_PATH": WHISPER_LIBS},
        capture=True,
        check=False,
    )
    if result.returncode != 0 or not srt_path.is_file():
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise RuntimeError(f"whisper failed: {detail[-1] if detail else 'unknown error'}")
    return srt_path


def parse_srt(path: Path) -> list[Segment]:
    segments: list[Segment] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    index = 0
    while index < len(lines):
        match = SRT_TIME_RE.search(lines[index])
        if not match:
            index += 1
            continue
        parts = [int(g) for g in match.groups()]
        start = parts[0] * 3600 + parts[1] * 60 + parts[2] + parts[3] / 1000
        end = parts[4] * 3600 + parts[5] * 60 + parts[6] + parts[7] / 1000
        index += 1
        body: list[str] = []
        while index < len(lines) and lines[index].strip():
            body.append(lines[index].strip())
            index += 1
        text = re.sub(r"\s+", " ", " ".join(body)).strip()
        if text:
            segments.append(Segment(start, end, text))
    return segments


def load_transcript(path: Path) -> Transcript:
    """Read a transcript from srt, vtt, or plain text."""
    suffix = path.suffix.lower()
    if suffix in (".srt", ".vtt"):
        segments = parse_srt(path)
        if segments:
            return Transcript(segments, has_timestamps=True, source=path)

    text = path.read_text(encoding="utf-8", errors="replace")
    # Plain whisper .txt output sometimes carries inline [HH:MM:SS] stamps.
    stamped = list(re.finditer(r"\[(\d{1,2}):(\d{2}):(\d{2})[.,]?\d*\]", text))
    if stamped:
        segments = []
        for i, match in enumerate(stamped):
            h, m, s = (int(g) for g in match.groups())
            start = h * 3600 + m * 60 + s
            body_end = stamped[i + 1].start() if i + 1 < len(stamped) else len(text)
            body = re.sub(r"\s+", " ", text[match.end():body_end]).strip()
            if body:
                segments.append(Segment(start, start, body))
        if segments:
            return Transcript(segments, has_timestamps=True, source=path)

    body = re.sub(r"\n{2,}", "\n", text).strip()
    if not body:
        return Transcript([], has_timestamps=False, source=path)
    return Transcript(split_plain_text(body), has_timestamps=False, source=path)


def split_plain_text(text: str, target_chars: int = 420) -> list[Segment]:
    """Break timestamp-less text into paragraph-sized pseudo-segments.

    Without this, a plain transcript is one indivisible block and cannot be
    chunked for a model with a small context window.
    """
    pieces = re.split(r"(?<=[.!?])\s+|\n+", text)
    segments: list[Segment] = []
    buffer: list[str] = []
    length = 0

    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        buffer.append(piece)
        length += len(piece) + 1
        if length >= target_chars:
            segments.append(Segment(0.0, 0.0, " ".join(buffer)))
            buffer, length = [], 0

    if buffer:
        segments.append(Segment(0.0, 0.0, " ".join(buffer)))
    return segments


def write_plain_text(transcript: Transcript, path: Path) -> None:
    ensure_dir(path.parent)
    path.write_text(
        "\n".join(seg.text for seg in transcript.segments) + "\n", encoding="utf-8"
    )


def merge_segments(segments: Sequence[Segment], target_chars: int = 420) -> list[Segment]:
    """Merge whisper's short segments into paragraph-sized timestamped blocks.

    Whisper emits a segment every few seconds, so one timestamp per segment
    would spend a meaningful share of the context window on timestamps alone.
    Blocks break on sentence boundaries once they reach the target size.
    """
    blocks: list[Segment] = []
    buffer: list[str] = []
    start = end = 0.0
    length = 0

    for segment in segments:
        if not buffer:
            start = segment.start
        buffer.append(segment.text)
        end = segment.end
        length += len(segment.text) + 1
        ends_sentence = segment.text.rstrip().endswith((".", "!", "?", '"', "…"))
        if length >= target_chars and (ends_sentence or length >= target_chars * 2):
            blocks.append(Segment(start, end, " ".join(buffer)))
            buffer, length = [], 0

    if buffer:
        blocks.append(Segment(start, end, " ".join(buffer)))
    return blocks


def render_blocks(blocks: Sequence[Segment], with_timestamps: bool) -> str:
    if not with_timestamps:
        return "\n\n".join(block.text for block in blocks)
    return "\n\n".join(f"[{block.stamp}] {block.text}" for block in blocks)


def split_oversized(blocks: Sequence[Segment], max_chars: int) -> Iterator[Segment]:
    """Hard-split any single block that is itself larger than a whole chunk.

    Only reachable with unpunctuated transcripts, but a block that cannot fit
    would otherwise silently produce an over-budget request.
    """
    for block in blocks:
        if len(block.text) <= max_chars:
            yield block
            continue
        words = block.text.split()
        buffer: list[str] = []
        length = 0
        for word in words:
            if length + len(word) + 1 > max_chars and buffer:
                yield Segment(block.start, block.end, " ".join(buffer))
                buffer, length = [], 0
            buffer.append(word)
            length += len(word) + 1
        if buffer:
            yield Segment(block.start, block.end, " ".join(buffer))


def chunk_blocks(blocks: Sequence[Segment], max_chars: int,
                 overlap_chars: int) -> list[list[Segment]]:
    """Split blocks into overlapping chunks that each fit `max_chars`.

    The overlap carries the tail of each chunk into the next so an argument
    split across the boundary is still visible whole in one of them.
    """
    if not blocks:
        return []

    chunks: list[list[Segment]] = []
    current: list[Segment] = []
    size = 0

    for block in split_oversized(blocks, max_chars):
        block_len = len(block.text) + 16  # timestamp + separators
        if current and size + block_len > max_chars:
            chunks.append(current)
            carry: list[Segment] = []
            carried = 0
            for previous in reversed(current):
                if carried >= overlap_chars:
                    break
                carry.insert(0, previous)
                carried += len(previous.text) + 16
            current = carry
            size = carried
        current.append(block)
        size += block_len

    if current:
        chunks.append(current)
    return chunks


# ──────────────────────────────────────────────────────────────────────────
# LLM providers
# ──────────────────────────────────────────────────────────────────────────

CONTEXT_LIMITS: dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4.1": 1_000_000,
    "gpt-4-turbo": 128_000,
    "gpt-5": 400_000,
    "gpt-5.6": 1_050_000,   # sol, terra, and luna all report 1.05M
    "gpt-6": 1_050_000,
    "o1": 200_000,
    "o3": 200_000,
    "o4": 200_000,
    "claude-3-5": 200_000,
    "claude-3-7": 200_000,
    "claude-opus": 200_000,
    "claude-sonnet": 200_000,
    "claude-haiku": 200_000,
    "gemini-1.5": 1_000_000,
    "gemini-2": 1_000_000,
    "llama-3": 128_000,
    "mistral": 32_000,
    "qwen": 32_000,
    "deepseek": 64_000,
}

# OpenAI reasoning families reject `max_tokens` and `temperature`. The guess
# only has to be close: Provider records what the API actually accepted on the
# first call and reuses that for the rest of the run.
REASONING_MODEL_RE = re.compile(r"^(o[1-9]|gpt-[56])")

# Above these input sizes a model switches to a more expensive long-context
# pricing tier, so it is worth telling the user before the bill arrives.
LONG_CONTEXT_THRESHOLDS: dict[str, int] = {
    "gpt-5.6": 272_000,
    "gpt-6": 272_000,
}


@dataclass
class Provider:
    name: str
    kind: str  # "openai" or "anthropic"
    base_url: str
    api_key: str
    model: str
    context_tokens: int
    max_tokens: int
    temperature: float
    timeout: int = 300
    retries: int = 4
    calls: int = field(default=0, init=False)
    prompt_tokens: int = field(default=0, init=False)
    completion_tokens: int = field(default=0, init=False)

    # Which parameter spelling this model accepts. Seeded from the model name,
    # then corrected by whatever the API actually accepts on the first call, so
    # a new model family costs at most one extra round trip per run.
    use_completion_tokens: bool = field(default=False, init=False)
    drop_temperature: bool = field(default=False, init=False)
    warned_long_context: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        model = self.model.split("/")[-1]
        # The token-limit rename is well established for these families, so
        # guessing it saves a wasted round trip.
        self.use_completion_tokens = (
            self.kind == "openai" and bool(REASONING_MODEL_RE.match(model))
        )
        # Temperature is left on deliberately. Low temperature is what keeps
        # extraction faithful, and assuming a model rejects it would silently
        # surrender that on every model that does support it. If the API does
        # reject it, the first call learns so and the rest of the run adapts.
        self.drop_temperature = False

    @property
    def label(self) -> str:
        return f"{self.name}:{self.model}"

    @property
    def long_context_threshold(self) -> int | None:
        lowered = self.model.lower()
        for prefix, threshold in LONG_CONTEXT_THRESHOLDS.items():
            if prefix in lowered:
                return threshold
        return None


PROVIDER_DEFAULTS = {
    "openai": {
        "kind": "openai",
        "base_url": "https://api.openai.com/v1",
        "key_env": ("OPENAI_API_KEY",),
        "model_env": ("DIGEST_MODEL", "OPENAI_MODEL"),
        "default_model": "gpt-5.6-luna",
    },
    "openrouter": {
        "kind": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": ("OPENROUTER_API_KEY",),
        "model_env": ("DIGEST_MODEL", "OPENROUTER_MODEL"),
        "default_model": "anthropic/claude-sonnet-4.5",
    },
    "anthropic": {
        "kind": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "key_env": ("ANTHROPIC_API_KEY",),
        "model_env": ("DIGEST_MODEL", "ANTHROPIC_MODEL"),
        "default_model": "claude-sonnet-4-5",
    },
    "lmstudio": {
        "kind": "openai",
        "base_url": None,  # from LM_STUDIO_URL
        "key_env": ("LM_STUDIO_API_KEY", "LM_STUDIO_API_TOKEN"),
        "model_env": ("DIGEST_MODEL", "LM_STUDIO_MODEL"),
        "default_model": None,  # auto-detected from the running server
    },
}


def guess_context_limit(model: str) -> int:
    lowered = model.lower()
    best = 0
    for prefix, limit in CONTEXT_LIMITS.items():
        if prefix in lowered:
            best = max(best, limit)
    return best or 128_000


def detect_provider(explicit: str | None) -> str:
    if explicit:
        return explicit
    from_env = os.environ.get("DIGEST_PROVIDER", "").strip().lower()
    if from_env:
        return from_env
    for name in ("openai", "anthropic", "openrouter"):
        for key in PROVIDER_DEFAULTS[name]["key_env"]:
            if os.environ.get(key):
                return name
    return "lmstudio"


def lmstudio_loaded_model(base_url: str, api_key: str) -> str | None:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.load(response)
        return (payload.get("data") or [{}])[0].get("id")
    except Exception:
        return None


def build_provider(args: argparse.Namespace) -> Provider:
    name = detect_provider(args.provider)
    if name not in PROVIDER_DEFAULTS:
        raise SystemExit(
            f"Unknown provider '{name}'. Choose from: "
            f"{', '.join(PROVIDER_DEFAULTS)}"
        )
    spec = PROVIDER_DEFAULTS[name]

    base_url = args.base_url or spec["base_url"]
    if name == "lmstudio":
        base_url = args.base_url or os.environ.get(
            "LM_STUDIO_URL", "http://localhost:1234/v1"
        )

    api_key = ""
    for key in spec["key_env"]:
        if os.environ.get(key):
            api_key = os.environ[key]
            break
    if name == "lmstudio":
        api_key = api_key or "lm-studio"
    elif not api_key:
        raise SystemExit(
            f"No API key for provider '{name}'. Set "
            f"{spec['key_env'][0]} in .env or the environment, "
            f"or pass --provider with one you have configured."
        )

    model = args.llm_model
    if not model:
        for key in spec["model_env"]:
            if os.environ.get(key):
                model = os.environ[key]
                break
    if not model and name == "lmstudio":
        model = lmstudio_loaded_model(base_url, api_key)
        if not model:
            raise SystemExit(
                "Could not auto-detect the LM Studio model. Load a model in "
                "LM Studio or pass --llm-model."
            )
    model = model or spec["default_model"]

    context_tokens = args.context_tokens or guess_context_limit(model)
    if name == "lmstudio" and not args.context_tokens:
        context_tokens = min(context_tokens, 32_000)

    return Provider(
        name=name,
        kind=spec["kind"],
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        model=model,
        context_tokens=context_tokens,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        timeout=args.timeout,
    )


class LLMError(RuntimeError):
    pass


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str],
              timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        message = body
        try:
            parsed = json.loads(body)
            message = (
                parsed.get("error", {}).get("message")
                if isinstance(parsed.get("error"), dict)
                else parsed.get("error")
            ) or body
        except json.JSONDecodeError:
            pass
        raise LLMError(f"HTTP {exc.code}: {str(message)[:500]}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"connection failed: {exc.reason}") from exc


def openai_payload(provider: Provider, system: str, user: str,
                   drop_temperature: bool, use_completion_tokens: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": provider.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if use_completion_tokens:
        payload["max_completion_tokens"] = provider.max_tokens
    else:
        payload["max_tokens"] = provider.max_tokens
    if not drop_temperature:
        payload["temperature"] = provider.temperature
    return payload


def warn_if_long_context(provider: Provider, system: str, user: str) -> None:
    """Warn once when a request crosses into a pricier long-context tier."""
    threshold = provider.long_context_threshold
    if not threshold or provider.warned_long_context:
        return
    estimated = est_tokens(system) + est_tokens(user)
    if estimated > threshold:
        provider.warned_long_context = True
        Log.warn(
            f"request is ~{estimated:,} input tokens, above the "
            f"{threshold:,}-token long-context threshold for {provider.model}; "
            "this is billed at the higher long-context rate"
        )


def call_llm(provider: Provider, system: str, user: str) -> str:
    """One completion, with retries and adaptive fixes for parameter churn."""
    warn_if_long_context(provider, system, user)
    use_completion_tokens = provider.use_completion_tokens
    drop_temperature = provider.drop_temperature

    last_error: Exception | None = None
    attempt = 0
    while attempt < provider.retries:
        try:
            if provider.kind == "anthropic":
                payload = {
                    "model": provider.model,
                    "max_tokens": provider.max_tokens,
                    "temperature": min(provider.temperature, 1.0),
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                }
                data = post_json(
                    f"{provider.base_url}/messages",
                    payload,
                    {
                        "x-api-key": provider.api_key,
                        "anthropic-version": "2023-06-01",
                    },
                    provider.timeout,
                )
                blocks = data.get("content") or []
                text = "".join(
                    block.get("text", "") for block in blocks
                    if block.get("type") == "text"
                )
                usage = data.get("usage") or {}
                provider.prompt_tokens += usage.get("input_tokens", 0)
                provider.completion_tokens += usage.get("output_tokens", 0)
                if data.get("stop_reason") == "max_tokens":
                    Log.warn(
                        "response hit the max-tokens ceiling and may be "
                        "truncated; raise --max-tokens"
                    )
            else:
                headers = {"Authorization": f"Bearer {provider.api_key}"}
                if provider.name == "openrouter":
                    headers["HTTP-Referer"] = "https://github.com/local/audio-digest"
                    headers["X-Title"] = "audio-digest"
                data = post_json(
                    f"{provider.base_url}/chat/completions",
                    openai_payload(
                        provider, system, user, drop_temperature, use_completion_tokens
                    ),
                    headers,
                    provider.timeout,
                )
                choice = (data.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                text = message.get("content") or message.get("reasoning_content") or ""
                usage = data.get("usage") or {}
                provider.prompt_tokens += usage.get("prompt_tokens", 0)
                provider.completion_tokens += usage.get("completion_tokens", 0)
                if choice.get("finish_reason") == "length":
                    Log.warn(
                        "response hit the max-tokens ceiling and may be "
                        "truncated; raise --max-tokens"
                    )

            provider.calls += 1
            # Remember any correction so the rest of the run gets it right.
            provider.use_completion_tokens = use_completion_tokens
            provider.drop_temperature = drop_temperature
            if not text.strip():
                raise LLMError("provider returned an empty completion")
            return text.strip()

        except LLMError as exc:
            last_error = exc
            message = str(exc).lower()

            # Adapting to a parameter rename is a correction, not a retry, so
            # it must not spend the budget reserved for transient failures.
            if "max_completion_tokens" in message and not use_completion_tokens:
                use_completion_tokens = True
                continue
            if "temperature" in message and "unsupported" in message and not drop_temperature:
                drop_temperature = True
                continue

            attempt += 1
            retryable = any(
                token in message
                for token in ("http 429", "http 500", "http 502", "http 503",
                              "http 504", "connection failed", "timed out",
                              "overloaded", "empty completion")
            )
            if not retryable or attempt >= provider.retries:
                break
            delay = min(60, 2 ** (attempt - 1) * 5)
            Log.warn(f"{exc} — retrying in {delay}s")
            time.sleep(delay)

    raise LLMError(str(last_error or "LLM call failed"))


# ──────────────────────────────────────────────────────────────────────────
# Prompts
# ──────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a rigorous analyst who compresses long-form spoken content — podcasts, \
interviews, panels, debates, lectures — into high-fidelity structured intelligence \
for a reader who will not watch the source.

Your priorities, in order:

1. Fidelity over concision. Never invent claims, numbers, names, studies, or \
citations. If the transcript is ambiguous, say it is ambiguous. A shorter report \
that loses a load-bearing argument is a failure; so is padding.
2. Attribution. This is multi-speaker content and the transcript carries no \
speaker labels. Infer who is speaking from self-introductions, direct address by \
name, turn-taking, and the host/guest dynamic. Mark every uncertain attribution \
with "(uncertain)". Never silently assign a claim to the wrong person — \
"Unattributed" is always better than a guess presented as fact.
3. Steel-manning before criticism. Represent each position at its strongest, in \
terms its holder would accept, before evaluating it. Keep the two activities in \
their own sections.
4. Traceability. Anchor substantive points to the [HH:MM:SS] timestamps that \
appear in the transcript so the reader can verify them against the source.
5. Quotation marks mean verbatim. Anything you put inside quotes must be copied \
character-for-character from the transcript. Never paraphrase into a quotation, \
never tidy up grammar or filler inside one, and never stitch together words \
spoken at different moments. Quotes are checked against the source after you \
write them, so an invented one will be caught and will discredit the report.

The transcript is machine-generated. Expect misheard proper nouns, wrong numbers, \
homophone errors, and missing punctuation. Flag terms that look garbled instead of \
silently guessing at them, and never let a transcription artifact become a claim \
you attribute to someone.

Write in plain, direct prose. Use markdown. No preamble, no restating the task, \
no closing summary of what you just wrote."""

CHUNK_PROMPT = """\
You are processing PART {{PART}} of {{TOTAL}} of a transcript from a single piece of \
content. Produce dense intermediate notes, not a summary. A later pass will \
synthesize these notes into the final report, so completeness matters far more \
than brevity, and you should not editorialize.

{{CONTEXT}}

Use exactly these sections. Under any section with nothing to report, write \
"None in this part."

### Speakers heard in this part
Each speaker as a name when the transcript establishes one, otherwise a stable \
positional descriptor such as "Host" or "Guest 1". Note their role and how you \
identified them. Append "(uncertain)" to a name you inferred but cannot confirm; \
never use "(uncertain)" as the label itself.

### Claims made
One bullet per distinct claim, including claims restated from earlier in the \
conversation. Format:
- **Speaker** {{STAMP_HINT}} — the claim in one sentence | type: empirical / predictive / causal / normative / anecdotal | support offered: what they cited, or "none"
Capture every substantive claim, including ones made in passing, and keep all \
numbers exactly as spoken.

### Evidence, data, and sources cited
- {{STAMP_HINT}} the figure, study, report, event, document, or person cited — cited by whom, to support what claim

### Predictions and conditionals
- **Speaker** {{STAMP_HINT}} — the prediction | timeframe: as stated, or "unspecified" | stated trigger or condition

### Verbatim quotes worth keeping
- {{STAMP_HINT}} **Speaker**: "exact quote"
Choose quotes that carry the speaker's reasoning or reveal their worldview, not \
filler. Copy each one character-for-character from the transcript: no \
paraphrasing, no cleaning up grammar, no combining words spoken at different \
moments. Omit a quote rather than approximating it.

### Topic shifts in this part
- {{STAMP_HINT}} short label for the new topic

### Transcript quality flags
Words, names, or numbers that look mis-transcribed, with your best guess at the \
intended term.

--- TRANSCRIPT PART {{PART}} OF {{TOTAL}} ---
{{TRANSCRIPT}}
--- END TRANSCRIPT PART ---"""

ANALYSIS_PROMPT = """\
Analyze the content below and produce a single markdown report. Follow the section \
template exactly: same headings, same order, nothing added, nothing dropped.

{{CONTEXT}}

## TL;DR
Five to eight sentences. A reader who stops here should still get the actual \
substance: the central thesis, who is arguing it, and what is at stake if it is \
right. No throat-clearing about what the video is.

## Context
The format (interview, panel, monologue, debate), the setting, and what prompted \
the conversation. Note the timeframe the discussion assumes and any events it \
treats as recent, since that dates its assumptions.

## Speakers
Give every speaker a short, stable label and reuse that exact label everywhere \
else in this report. Use a real name when the transcript establishes one; \
otherwise use a positional label that still distinguishes them, such as "Host", \
"Guest 1 (entrepreneur)", or "Panelist 2". Never use "(uncertain)" as a label by \
itself — it is a qualifier you append to a name you inferred but cannot confirm, \
as in "Rapelang Rabana (uncertain)". One line each:
- **Label** — role or affiliation as stated — the position they occupy in the conversation
If the transcript genuinely does not let you tell two voices apart, say so in one \
line here and label them "Unattributed" below. Do not produce a report in which \
every claim carries the same meaningless tag; distinguishing who said what is the \
main thing this section exists to do.

## Major Themes
Three to seven themes, each under its own `###` subheading, ordered by how much \
weight the conversation actually gives them rather than when they come up. For each:
- **The idea**: stated in the speakers' own framing.
- **How it develops**: where it surfaces and how the discussion moves it forward.
- **Anchors**: {{STAMP_HINT}} for the moments that matter.

## Claims by Speaker
A `###` subheading per speaker, using the exact labels from the Speakers section, \
then bullets:
- the claim — *type* (empirical / predictive / causal / normative / anecdotal), support offered: what they cited, or "asserted without support" {{STAMP_HINT}}
Include every claim that is load-bearing for their argument, even ones made in \
passing. Keep numbers exactly as spoken. Separate what a speaker asserts from what \
they merely entertain or attribute to someone else.

## Steel-Manned Conclusions
The strongest, most coherent version of what this content concludes, written as a \
proponent who is also a careful thinker would write it. Repair sloppy phrasing, \
supply the implicit premises the speakers rely on but never state, and present the \
argument in its best form. Do not smuggle in criticism here — that is the next \
section. Where speakers hold genuinely different positions, steel-man each one \
under its own `###` subheading.

## Corroborating Evidence
The evidence offered in support of the main claims. For each: what it is, who cited \
it, which claim it supports, and how strong it actually is as support — direct, \
suggestive, anecdotal, or merely illustrative. Keep evidence that is checkable \
against public sources separate from evidence that rests only on a speaker's own \
testimony, access, or experience. Do not introduce outside evidence the transcript \
never mentions.

## Critical Read
Where the argument is weakest. Cover whichever apply: unsupported leaps from \
premise to conclusion, confidence that outruns the evidence, counterarguments that \
go missing or get strawmanned, selection effects and base-rate problems, terms that \
shift meaning mid-argument, incentives or conflicts of interest a listener should \
weigh, and the specific conditions under which the thesis would turn out wrong. Be \
concrete, cite {{STAMP_HINT}}, and criticize the argument rather than the speaker.

## Falsifiable Predictions & Tripwires
A markdown table with columns: Prediction | Speaker | Timeframe | Would confirm | \
Would refute. Include only rows that are genuinely checkable against future \
observation. If there are none, write "No falsifiable predictions made."

## Notable Quotes
Four to eight quotes as blockquotes, each followed by the speaker label and \
{{STAMP_HINT}}. Choose quotes that carry reasoning or reveal a worldview rather \
than ones that merely sound striking.

Every quote must be a span copied character-for-character from the transcript. Do \
not paraphrase, do not repair grammar or remove filler, do not combine words from \
different moments, and never convert your own summary of a point into a quotation. \
Mark words cut from the middle of a span with an ellipsis. If you cannot find \
enough exact spans worth quoting, return fewer — four faithful quotes are worth \
far more than eight polished inventions.

## Open Questions & What to Verify
What a listener should look up before acting on any of this, most important first.

## Fidelity Notes
Transcript quality, likely mis-transcribed names, numbers, and jargon, audible gaps \
or crosstalk, and anything in this report you hold with low confidence.

--- {{SOURCE_LABEL}} ---
{{TRANSCRIPT}}
--- END ---"""

ROLLUP_PROMPT = """\
You are synthesizing {{COUNT}} analytical reports, each covering one video from the \
collection "{{TITLE}}", into a single cross-cutting briefing. The reader has already \
had each video compressed into a report; your job is to tell them what the set adds \
up to, which they cannot get from any single report.

Produce markdown using exactly these sections.

## Landscape
What this body of content collectively covers, who the recurring voices are, and \
what question the collection as a whole is circling. Four to eight sentences.

## Consensus
Points where sources independently agree. For each: the point, which sources \
support it, and whether the agreement reflects independent convergence or shared \
sources and priors — that distinction matters more than the agreement itself.

## Live Disagreements
A markdown table: Question | Position A (who) | Position B (who) | What would settle it. \
One row per substantive disagreement. Include disagreements of emphasis and framing, \
not just direct contradictions.

## Distinctive & Outlier Views
Positions held by only one source that the others do not engage with. Note whether \
each looks neglected or simply weak, and say which.

## Shared Evidence Base
Sources, datasets, figures, and events that recur across videos. Flag any that a lot \
of conclusions rest on, since a single weak load-bearing source is a shared failure \
point for the whole set.

## Tracked Predictions
A markdown table: Prediction | Source (video) | Speaker | Timeframe | Would refute. \
Consolidate every checkable prediction across all reports. Merge near-duplicates and \
note when several speakers make the same call.

## Model Update
The part that matters most. Given this set, what should the reader now believe \
differently than before? Separate:
- **Raise confidence in**: with the reason the collection moves you here.
- **Lower confidence in**: same.
- **Newly on the radar**: ideas worth tracking that were not previously in view.
- **Unresolved**: what stays genuinely open, and what evidence would close it.
Be specific and honest about how much this collection actually licenses. If the \
sources share a worldview, say so — agreement among people who already agree is \
weak evidence, and the reader should discount accordingly.

## What to Watch
Concrete, near-term, observable indicators that would tell the reader which of the \
competing readings is right.

## Reading Priority
Rank the individual reports, highest value first, with a one-line justification each. \
Note any that are safe to skip and why.

--- REPORTS ---
{{REPORTS}}
--- END REPORTS ---"""

PROMPT_FILES = {
    "system": "system.md",
    "chunk": "chunk-notes.md",
    "analysis": "analysis.md",
    "rollup": "rollup.md",
}
DEFAULT_PROMPTS = {
    "system": SYSTEM_PROMPT,
    "chunk": CHUNK_PROMPT,
    "analysis": ANALYSIS_PROMPT,
    "rollup": ROLLUP_PROMPT,
}


def load_prompts(prompts_dir: Path | None) -> dict[str, str]:
    """Default prompts, overridden by any files present in `prompts_dir`."""
    prompts = dict(DEFAULT_PROMPTS)
    if not prompts_dir or not prompts_dir.is_dir():
        return prompts
    for name, filename in PROMPT_FILES.items():
        path = prompts_dir / filename
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                prompts[name] = text
                Log.detail(f"using custom prompt: {path.name}")
    return prompts


def write_default_prompts(prompts_dir: Path) -> None:
    ensure_dir(prompts_dir)
    for name, filename in PROMPT_FILES.items():
        path = prompts_dir / filename
        path.write_text(DEFAULT_PROMPTS[name] + "\n", encoding="utf-8")
        print(f"  wrote {path}")


def fill(template: str, **values: Any) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", str(value))
    return out


def build_context_note(ref: VideoRef, part: int | None = None,
                       total: int | None = None) -> str:
    """The metadata block prepended to every prompt, so the model knows the source."""
    lines = ["Source metadata:", f"- Title: {ref.display}"]
    if ref.channel:
        lines.append(f"- Channel: {ref.channel}")
    if ref.upload_date and len(str(ref.upload_date)) == 8:
        date = str(ref.upload_date)
        lines.append(f"- Published: {date[:4]}-{date[4:6]}-{date[6:8]}")
    if ref.duration:
        lines.append(f"- Duration: {format_duration(ref.duration)}")
    if ref.video_id:
        lines.append(f"- URL: {ref.watch_url}")
    if ref.note:
        lines.append(
            f"- Why the reader saved this: {ref.note} "
            "(weight the analysis toward this interest without ignoring the rest)"
        )
    if part and total and total > 1:
        lines.append(f"- This is part {part} of {total} of the transcript.")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────
# Analysis
# ──────────────────────────────────────────────────────────────────────────

STAMP_HINT_ON = "[HH:MM:SS]"
STAMP_HINT_OFF = "(no timestamps available in this transcript; omit them)"


def chunk_budget_chars(provider: Provider, overhead_tokens: int) -> int:
    """How many transcript characters fit in one request to this model."""
    usable = provider.context_tokens - provider.max_tokens - overhead_tokens - 1500
    if usable < 2000:
        raise SystemExit(
            f"Context window too small: {provider.model} reports "
            f"{provider.context_tokens} tokens but --max-tokens is "
            f"{provider.max_tokens}. Lower --max-tokens or raise --context-tokens."
        )
    return int(usable * CHARS_PER_TOKEN)


def analyze_transcript(provider: Provider, prompts: dict[str, str], ref: VideoRef,
                       transcript: Transcript, force_chunk: bool,
                       notes_path: Path | None) -> tuple[str, int]:
    """Produce the per-video report. Returns (markdown, chunks_used)."""
    stamp_hint = STAMP_HINT_ON if transcript.has_timestamps else STAMP_HINT_OFF
    blocks = merge_segments(transcript.segments) if transcript.has_timestamps \
        else list(transcript.segments)
    full_text = render_blocks(blocks, transcript.has_timestamps)

    system = prompts["system"]
    overhead = est_tokens(system) + est_tokens(prompts["analysis"]) + 400
    max_chars = chunk_budget_chars(provider, overhead)

    fits = len(full_text) <= max_chars
    if fits and not force_chunk:
        Log.detail(
            f"single pass: ~{est_tokens(full_text):,} transcript tokens "
            f"(limit ~{int(max_chars / CHARS_PER_TOKEN):,})"
        )
        user = fill(
            prompts["analysis"],
            CONTEXT=build_context_note(ref),
            STAMP_HINT=stamp_hint,
            SOURCE_LABEL="TRANSCRIPT",
            TRANSCRIPT=full_text,
        )
        return call_llm(provider, system, user), 1

    # Map: dense notes per chunk. Reduce: synthesize the report from the notes.
    chunk_overhead = est_tokens(system) + est_tokens(prompts["chunk"]) + 400
    chunk_chars = chunk_budget_chars(provider, chunk_overhead)
    chunks = chunk_blocks(blocks, chunk_chars, overlap_chars=min(1500, chunk_chars // 10))
    total = len(chunks)
    Log.detail(
        f"map-reduce: {total} chunks "
        f"(~{est_tokens(full_text):,} transcript tokens, "
        f"~{int(chunk_chars / CHARS_PER_TOKEN):,} per chunk)"
    )

    notes: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        Log.detail(f"chunk {index}/{total} [{chunk[0].stamp}–{chunk[-1].stamp}]")
        user = fill(
            prompts["chunk"],
            PART=index,
            TOTAL=total,
            CONTEXT=build_context_note(ref, index, total),
            STAMP_HINT=stamp_hint,
            TRANSCRIPT=render_blocks(chunk, transcript.has_timestamps),
        )
        notes.append(
            f"## Notes from part {index} of {total} "
            f"[{chunk[0].stamp}–{chunk[-1].stamp}]\n\n{call_llm(provider, system, user)}"
        )

    if notes_path:
        ensure_dir(notes_path.parent)
        notes_path.write_text("\n\n---\n\n".join(notes) + "\n", encoding="utf-8")

    combined = fold_notes(provider, prompts, ref, notes, max_chars, stamp_hint)
    user = fill(
        prompts["analysis"],
        CONTEXT=(
            build_context_note(ref)
            + "\n\nYou are working from ordered intermediate notes covering the whole "
            "transcript in sequence, not the raw transcript. Treat them as faithful "
            "and synthesize across all of them. The same point may appear in several "
            "parts; merge duplicates rather than listing them twice, and preserve "
            "every distinct claim, number, and timestamp the notes recorded."
        ),
        STAMP_HINT=stamp_hint,
        SOURCE_LABEL="TRANSCRIPT NOTES",
        TRANSCRIPT=combined,
    )
    return call_llm(provider, system, user), total


def fold_notes(provider: Provider, prompts: dict[str, str], ref: VideoRef,
               notes: list[str], max_chars: int, stamp_hint: str) -> str:
    """Consolidate notes in groups until they fit one request.

    Only matters for very long content on small-context models, where even the
    intermediate notes overflow the window.
    """
    combined = "\n\n---\n\n".join(notes)
    rounds = 0
    while len(combined) > max_chars and len(notes) > 1 and rounds < 3:
        rounds += 1
        Log.detail(f"notes exceed context; consolidating (round {rounds})")
        group_size = max(2, math.ceil(len(notes) / math.ceil(len(combined) / max_chars)))
        folded: list[str] = []
        for start in range(0, len(notes), group_size):
            group = notes[start:start + group_size]
            if len(group) == 1:
                folded.append(group[0])
                continue
            user = fill(
                prompts["chunk"],
                PART=f"{start + 1}-{start + len(group)}",
                TOTAL=len(notes),
                CONTEXT=(
                    build_context_note(ref)
                    + "\n\nYou are merging already-extracted notes from consecutive "
                    "parts, not raw transcript. Preserve every distinct claim, "
                    "number, quote, and timestamp; merge only exact duplicates."
                ),
                STAMP_HINT=stamp_hint,
                TRANSCRIPT="\n\n---\n\n".join(group),
            )
            folded.append(call_llm(provider, prompts["system"], user))
        notes = folded
        combined = "\n\n---\n\n".join(notes)

    if len(combined) > max_chars:
        Log.warn("notes still exceed the context window; truncating the tail")
        combined = combined[:max_chars]
    return combined


# ──────────────────────────────────────────────────────────────────────────
# Report writing
# ──────────────────────────────────────────────────────────────────────────


def write_report(path: Path, ref: VideoRef, body: str, provider: Provider,
                 whisper_model: str, chunks: int, transcript: Transcript,
                 unverified_quotes: int | None = None) -> None:
    ensure_dir(path.parent)
    published = ""
    if ref.upload_date and len(str(ref.upload_date)) == 8:
        date = str(ref.upload_date)
        published = f"{date[:4]}-{date[4:6]}-{date[6:8]}"

    front = {
        "title": ref.display,
        "video_id": ref.video_id or "",
        "url": ref.watch_url if ref.video_id else ref.url,
        "channel": ref.channel or "",
        "published": published,
        "duration": format_duration(ref.duration or transcript.duration),
        "whisper_model": whisper_model,
        "llm": provider.label,
        "chunks": chunks,
        "analyzed": utc_stamp(),
    }
    if unverified_quotes is not None:
        front["unverified_quotes"] = unverified_quotes

    lines = ["---"]
    for key, value in front.items():
        rendered = str(value).replace('"', "'")
        lines.append(f'{key}: "{rendered}"' if isinstance(value, str) else f"{key}: {value}")
    lines += ["---", "", f"# {ref.display}", ""]

    meta_bits = [bit for bit in (ref.channel, published,
                                 format_duration(ref.duration or transcript.duration))
                 if bit and bit != "unknown"]
    if meta_bits:
        lines.append(" · ".join(meta_bits))
        lines.append("")
    if ref.video_id:
        lines += [f"[Watch on YouTube]({ref.watch_url})", ""]
    if ref.note:
        lines += [f"> **Saved because:** {ref.note}", ""]
    lines += ["---", "", body, ""]

    path.write_text("\n".join(lines), encoding="utf-8")


def timestamp_links(body: str, video_id: str | None) -> str:
    """Turn [HH:MM:SS] stamps into deep links into the video."""
    if not video_id:
        return body

    def repl(match: re.Match[str]) -> str:
        hours, minutes, seconds = (int(g) for g in match.groups())
        total = hours * 3600 + minutes * 60 + seconds
        return (
            f"[{match.group(0)[1:-1]}]"
            f"(https://www.youtube.com/watch?v={video_id}&t={total}s)"
        )

    return re.sub(r"\[(\d{1,2}):(\d{2}):(\d{2})\]", repl, body)


QUOTE_BLOCK_RE = re.compile(r"(?:^>.*(?:\n|$))+", re.MULTILINE)
UNVERIFIED_MARKER = "⚠︎ *Not found verbatim in the transcript — treat as paraphrase.*"


def normalize_for_match(text: str) -> str:
    """Collapse to lowercase words so punctuation and spacing do not matter."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def extract_quote_text(block: str) -> str:
    """Pull the quoted words out of a markdown blockquote, dropping attribution."""
    lines = [re.sub(r"^>\s?", "", line).strip() for line in block.strip().splitlines()]
    text = " ".join(line for line in lines if line).strip()
    text = re.sub(r"\[\d{1,2}:\d{2}:\d{2}\](?:\([^)]*\))?", "", text).strip()

    quoted = re.findall(r"[\"“]([^\"“”]{12,})[\"”]", text)
    if quoted:
        return max(quoted, key=len)

    # A trailing "— Speaker" attribution is not part of the quotation.
    trailing = re.search(r"\s[—–]\s*\**[^—–]{1,60}\**\s*$", text)
    if trailing:
        text = text[: trailing.start()]
    return text.strip().strip("\"“”*").strip()


def quote_support_ratio(quote: str, haystack: str, gram: int = 4) -> float:
    """How much of a quote is traceable to the source, as n-gram coverage.

    Measured over overlapping word n-grams rather than one contiguous run, so a
    single misheard word inside an otherwise verbatim quote does not sink it,
    while a paraphrase still scores low.
    """
    words = normalize_for_match(quote).split()
    if not words:
        return 1.0
    joined = " ".join(words)
    if joined in haystack:
        return 1.0
    if len(words) < gram:
        return 0.0
    grams = [" ".join(words[i:i + gram]) for i in range(len(words) - gram + 1)]
    return sum(1 for g in grams if g in haystack) / len(grams)


def verify_quotes(body: str, transcript: Transcript,
                  threshold: float = 0.6) -> tuple[str, int]:
    """Flag blockquotes that do not appear in the transcript.

    Models paraphrase into quotation marks under pressure to produce a fixed
    number of quotes. The transcript is right here, so check rather than trust.
    """
    haystack = normalize_for_match(" ".join(seg.text for seg in transcript.segments))
    if not haystack:
        return body, 0

    unverified = 0

    def check(match: re.Match[str]) -> str:
        nonlocal unverified
        block = match.group(0)
        quote = extract_quote_text(block)
        words = normalize_for_match(quote).split()
        # Very short fragments produce too many false positives to be useful.
        if len(words) < 5 or UNVERIFIED_MARKER in block:
            return block
        if quote_support_ratio(quote, haystack) >= threshold:
            return block
        unverified += 1
        # Kept inside the blockquote so the warning travels with the quote and
        # a second pass over the same text recognizes it as already flagged.
        return f"{block.rstrip()}\n>\n> {UNVERIFIED_MARKER}\n"

    return QUOTE_BLOCK_RE.sub(check, body), unverified


def write_rollup(path: Path, title: str, body: str, provider: Provider,
                 entries: list[dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    lines = [
        f"# {title}",
        "",
        f"_{len(entries)} videos · {provider.label} · {utc_stamp()}_",
        "",
        "---",
        "",
        body,
        "",
        "---",
        "",
        "## Sources",
        "",
    ]
    for entry in entries:
        link = f"[{entry['title']}](videos/{Path(entry['report']).name})"
        extras = [bit for bit in (entry.get("channel"), entry.get("duration")) if bit]
        suffix = f" — {' · '.join(extras)}" if extras else ""
        if entry.get("url"):
            suffix += f" · [source]({entry['url']})"
        lines.append(f"- {link}{suffix}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def trim_reports_to_budget(entries: list[dict[str, Any]], bodies: list[str],
                           max_chars: int) -> str:
    """Assemble reports for the rollup, trimming evenly if they overflow."""
    def assemble(limit: int | None) -> str:
        parts = []
        for index, (entry, body) in enumerate(zip(entries, bodies), start=1):
            text = body
            if limit and len(text) > limit:
                text = text[:limit].rstrip() + "\n\n_[report truncated for synthesis]_"
            parts.append(f"### Report {index}: {entry['title']}\n\n{text}")
        return "\n\n---\n\n".join(parts)

    combined = assemble(None)
    if len(combined) <= max_chars:
        return combined

    limit = max(1500, max_chars // max(1, len(bodies)) - 200)
    Log.warn(
        f"{len(bodies)} reports exceed the context window; "
        f"trimming each to ~{limit:,} chars for the roll-up"
    )
    return assemble(limit)[:max_chars]


# ──────────────────────────────────────────────────────────────────────────
# State
# ──────────────────────────────────────────────────────────────────────────


class State:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {"videos": {}}
        if path.is_file():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
                self.data.setdefault("videos", {})
            except (json.JSONDecodeError, OSError):
                pass

    def get(self, key: str) -> dict[str, Any]:
        return self.data["videos"].get(key, {})

    def record(self, key: str, **fields: Any) -> None:
        entry = self.data["videos"].setdefault(key, {})
        entry.update(fields)
        entry["updated"] = utc_stamp()
        self.save()

    def save(self) -> None:
        ensure_dir(self.path.parent)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="digest.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "YouTube playlist or video list -> whisper.cpp transcripts -> "
            "structured analytical reports."
        ),
        epilog="""\
inputs may be mixed and matched:
  a playlist or channel URL      https://youtube.com/playlist?list=...
  one or more video URLs         https://youtube.com/watch?v=...
  a list file                    videos.csv | videos.json | videos.yaml | videos.txt
  local media or transcripts     downloads/talk.mp3 | transcripts/talk.txt

list file formats:
  txt    one URL per line; '#' comments; optional  URL | why I saved it
  csv    a 'url' column, optionally 'title' and 'note'; or bare URLs in column 1
  json   ["url", ...] or [{"url":..., "note":...}] or {"title":..., "videos":[...]}
  yaml   a list of URLs or of mappings, optionally under a 'videos:' key

examples:
  digest.py "https://www.youtube.com/playlist?list=PLxxxx"
  digest.py --title "Macro Week 37" watchlist.csv
  digest.py --provider anthropic --llm-model claude-sonnet-4-5 "https://youtu.be/xxxx"
  digest.py -m small.en --limit 5 "https://youtube.com/playlist?list=PLxxxx"
  digest.py --provider lmstudio transcripts/*.txt
  digest.py --dry-run "https://youtube.com/playlist?list=PLxxxx"
""",
    )

    parser.add_argument("inputs", nargs="*", metavar="INPUT",
                        help="playlist URL, video URL(s), list file, or local media")

    run_group = parser.add_argument_group("run")
    run_group.add_argument("--title", help="run title (default: playlist title or date)")
    run_group.add_argument("-o", "--output", default=str(SCRIPT_DIR / "digests"),
                           metavar="DIR", help="output root (default: ./digests)")
    run_group.add_argument("--limit", type=int, metavar="N",
                           help="process at most N videos")
    run_group.add_argument("--force", action="store_true",
                           help="reprocess videos that already have a report")
    run_group.add_argument("--retranscribe", action="store_true",
                           help="re-run whisper even if a transcript exists")
    run_group.add_argument("--single", action="store_true",
                           help="treat a watch URL containing &list= as one video")
    run_group.add_argument("--dry-run", action="store_true",
                           help="resolve the video list and estimate work, then stop")
    run_group.add_argument("--no-rollup", action="store_true",
                           help="skip the cross-video synthesis")
    run_group.add_argument("-q", "--quiet", action="store_true",
                           help="only report errors")

    ts_group = parser.add_argument_group("transcription")
    ts_group.add_argument("-m", "--model", default="base.en", metavar="MODEL",
                          help="whisper model: tiny.en, base.en, small.en, "
                               "medium.en, large-v3-turbo (default: base.en)")
    ts_group.add_argument("-l", "--language", default="en", metavar="LANG",
                          help="spoken language, or 'auto' (default: en)")
    ts_group.add_argument("-t", "--threads", type=int, default=4, metavar="N",
                          help="whisper CPU threads (default: 4)")
    ts_group.add_argument("-f", "--audio-format", default="mp3",
                          choices=["mp3", "wav", "flac", "ogg"],
                          help="download audio format (default: mp3)")
    ts_group.add_argument("-s", "--safe", action="store_true",
                          help="rate-limit downloads")
    ts_group.add_argument("--transcribe-only", action="store_true",
                          help="download and transcribe, skip all LLM analysis")

    llm_group = parser.add_argument_group("analysis")
    llm_group.add_argument("--provider", choices=sorted(PROVIDER_DEFAULTS),
                           help="LLM provider (default: first configured API key)")
    llm_group.add_argument("--llm-model", metavar="MODEL",
                           help="model name (default: provider default or env)")
    llm_group.add_argument("--base-url", metavar="URL",
                           help="override the provider API base URL")
    llm_group.add_argument("--max-tokens", type=int, default=8192, metavar="N",
                           help="max response tokens (default: 8192)")
    llm_group.add_argument("--temperature", type=float, default=0.2, metavar="N",
                           help="sampling temperature (default: 0.2)")
    llm_group.add_argument("--context-tokens", type=int, metavar="N",
                           help="override the model's context window")
    llm_group.add_argument("--chunk", action="store_true",
                           help="always map-reduce, even when the transcript fits")
    llm_group.add_argument("--no-verify-quotes", action="store_true",
                           help="skip checking quotes against the transcript")
    llm_group.add_argument("--timeout", type=int, default=300, metavar="SEC",
                           help="per-request timeout (default: 300)")
    llm_group.add_argument("--prompts-dir", default=str(SCRIPT_DIR / "prompts"),
                           metavar="DIR",
                           help="directory of prompt overrides (default: ./prompts)")
    llm_group.add_argument("--write-prompts", action="store_true",
                           help="write the default prompts to --prompts-dir and exit")
    return parser


def preflight(needs_download: bool, needs_whisper: bool) -> None:
    if needs_download and not shutil.which("yt-dlp"):
        raise SystemExit("yt-dlp not found on PATH. Install it: brew install yt-dlp")
    if needs_whisper and not WHISPER_BIN.is_file():
        raise SystemExit(
            f"whisper binary not found at {WHISPER_BIN}\n"
            "Build it with ./setup.sh, or pass transcripts instead of URLs."
        )


def print_plan(refs: list[VideoRef], title: str, out_dir: Path,
               provider: Provider | None, args: argparse.Namespace) -> None:
    Log.say("=" * 60)
    Log.say("  YouTube -> Transcript -> Analysis")
    Log.say("=" * 60)
    Log.say()
    Log.say(f"  Title:    {title}")
    Log.say(f"  Videos:   {len(refs)}")
    Log.say(f"  Whisper:  {args.model} ({args.language}, {args.threads} threads)")
    if provider:
        Log.say(
            f"  LLM:      {provider.label} "
            f"(context ~{provider.context_tokens:,} tok, "
            f"max out {provider.max_tokens:,})"
        )
    else:
        Log.say("  LLM:      skipped (--transcribe-only)")
    Log.say(f"  Output:   {out_dir}")
    Log.say()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    Log.quiet = args.quiet

    if args.write_prompts:
        write_default_prompts(Path(args.prompts_dir).expanduser())
        print("\nEdit these files to customize the analysis; "
              "digest.py picks them up automatically.")
        return 0

    if not args.inputs:
        parser.error("no input given. Pass a playlist URL, video URL, or list file.")

    load_env_file(SCRIPT_DIR / ".env")

    Log.step("Resolve", f"{len(args.inputs)} input(s)")
    refs, discovered_title = resolve_inputs(
        args.inputs, args.limit, expand=not args.single
    )
    if not refs:
        Log.error("no videos resolved from the given input.")
        return 1

    title = args.title or discovered_title or f"Digest {datetime.now():%Y-%m-%d}"
    out_dir = Path(args.output).expanduser() / slugify(title)
    needs_download = any(
        ref.local_audio is None and ref.local_transcript is None for ref in refs
    )
    preflight(needs_download, needs_whisper=any(
        ref.local_transcript is None for ref in refs
    ))

    provider = None if args.transcribe_only else build_provider(args)
    print_plan(refs, title, out_dir, provider, args)

    if args.dry_run:
        Log.say("  Resolved videos:")
        known = sum(1 for ref in refs if ref.duration)
        total_seconds = sum(ref.duration or 0 for ref in refs)
        for index, ref in enumerate(refs, start=1):
            bits = [format_duration(ref.duration)] if ref.duration else []
            if ref.channel:
                bits.insert(0, ref.channel)
            suffix = f"  ({' · '.join(bits)})" if bits else ""
            Log.say(f"    {index:3d}. {ref.display}{suffix}")
            if ref.note:
                Log.say(f"         note: {ref.note}")
        Log.say()
        if total_seconds:
            Log.say(
                f"  Known runtime: {format_duration(total_seconds)} across "
                f"{known}/{len(refs)} videos"
            )
            # ~150 spoken words/min, ~1.35 tokens/word.
            est = int(total_seconds / 60 * 150 * 1.35)
            Log.say(f"  Rough transcript size: ~{est:,} tokens total")
        Log.say("  Dry run: nothing downloaded, transcribed, or sent to a provider.")
        return 0

    ensure_dir(out_dir)
    videos_dir = ensure_dir(out_dir / "videos")
    transcripts_dir = ensure_dir(out_dir / "transcripts")
    notes_dir = out_dir / "notes"
    download_dir = SCRIPT_DIR / "downloads"
    state = State(out_dir / "state.json")
    state.data.update({"title": title, "output": str(out_dir)})

    prompts = load_prompts(Path(args.prompts_dir).expanduser())

    entries: list[dict[str, Any]] = []
    bodies: list[str] = []
    failures: list[tuple[str, str]] = []
    skipped = 0
    transcribed = 0

    for index, ref in enumerate(refs, start=1):
        Log.say("-" * 60)
        Log.step(f"{index}/{len(refs)}", ref.display)

        previous = state.get(ref.key)
        report_path_str = previous.get("report")
        if (
            not args.force
            and previous.get("status") == "done"
            and report_path_str
            and Path(report_path_str).is_file()
        ):
            Log.detail("already analyzed; skipping (use --force to redo)")
            entries.append(previous)
            bodies.append(
                Path(report_path_str).read_text(encoding="utf-8", errors="replace")
            )
            skipped += 1
            continue

        try:
            # ── Transcript ────────────────────────────────────────────
            if ref.local_transcript:
                transcript = load_transcript(ref.local_transcript)
                srt_path = ref.local_transcript
                Log.detail(f"using transcript: {ref.local_transcript.name}")
            else:
                if ref.local_audio:
                    audio_path = ref.local_audio
                    Log.detail(f"using local audio: {audio_path.name}")
                else:
                    Log.detail("downloading audio")
                    audio_path = download_audio(
                        ref, download_dir, args.audio_format, args.safe
                    )
                    Log.detail(f"audio: {audio_path.name}")

                # Cached audio may predate the sidecar metadata file, which
                # would otherwise leave the report titled with a bare video id.
                if not ref.title and ref.video_id:
                    fetch_metadata(ref)

                slug = slugify(ref.title or audio_path.stem)
                if ref.video_id:
                    slug = f"{slug}-{ref.video_id}"
                srt_path = transcribe(
                    audio_path,
                    transcripts_dir / slug,
                    args.model,
                    args.language,
                    args.threads,
                    args.retranscribe,
                )
                transcript = load_transcript(srt_path)
                write_plain_text(transcript, srt_path.with_suffix(".txt"))

            if not transcript.segments:
                raise RuntimeError("transcript is empty")

            words = sum(len(seg.text.split()) for seg in transcript.segments)
            Log.detail(
                f"transcript: {words:,} words, "
                f"{format_duration(transcript.duration or ref.duration)}"
            )

            if args.transcribe_only:
                state.record(ref.key, status="transcribed", title=ref.display,
                             transcript=str(srt_path), url=ref.watch_url)
                transcribed += 1
                continue

            # ── Analysis ──────────────────────────────────────────────
            assert provider is not None
            slug = slugify(ref.title or (srt_path.stem if srt_path else "video"))
            report_path = videos_dir / f"{slug}.md"
            body, chunks = analyze_transcript(
                provider,
                prompts,
                ref,
                transcript,
                args.chunk,
                notes_dir / f"{slug}.notes.md",
            )
            checked = body
            unverified = None
            if not args.no_verify_quotes:
                checked, unverified = verify_quotes(body, transcript)
                if unverified:
                    Log.warn(
                        f"{unverified} quote(s) not found verbatim in the "
                        "transcript; flagged in the report"
                    )

            linked = timestamp_links(checked, ref.video_id)
            write_report(report_path, ref, linked, provider, args.model,
                         chunks, transcript, unverified)

            Log.detail(f"report: {report_path.relative_to(out_dir)} "
                       f"({len(body.split()):,} words)")

            entry = {
                "title": ref.display,
                "video_id": ref.video_id or "",
                "url": ref.watch_url if ref.video_id else ref.url,
                "channel": ref.channel or "",
                "duration": format_duration(ref.duration or transcript.duration),
                "report": str(report_path),
                "transcript": str(srt_path),
                "chunks": chunks,
                "status": "done",
            }
            entries.append(entry)
            bodies.append(body)
            state.record(ref.key, **entry)

        except (RuntimeError, LLMError, OSError, subprocess.SubprocessError) as exc:
            Log.error(f"{ref.display}: {exc}")
            failures.append((ref.display, str(exc)))
            state.record(ref.key, status="failed", title=ref.display, error=str(exc))

    # ── Roll-up ──────────────────────────────────────────────────────
    Log.say("-" * 60)
    rollup_path = out_dir / "index.md"
    # Re-synthesize when there is new material, or when a previous run never
    # produced an index (a failed roll-up should not be sticky).
    wants_rollup = (
        provider is not None
        and not args.no_rollup
        and len(entries) > 1
        and (len(entries) > skipped or not rollup_path.is_file())
    )
    if wants_rollup:
        try:
            Log.step("Roll-up", f"synthesizing {len(entries)} reports")
            overhead = est_tokens(prompts["system"]) + est_tokens(prompts["rollup"]) + 400
            budget = chunk_budget_chars(provider, overhead)
            combined = trim_reports_to_budget(entries, bodies, budget)
            body = call_llm(
                provider,
                prompts["system"],
                fill(prompts["rollup"], COUNT=len(entries), TITLE=title,
                     REPORTS=combined),
            )
            write_rollup(rollup_path, title, body, provider, entries)
            Log.detail(f"index: {rollup_path.name} ({len(body.split()):,} words)")
        except LLMError as exc:
            Log.error(f"roll-up failed: {exc}")
            failures.append(("cross-video roll-up", str(exc)))
    elif provider and not args.no_rollup:
        if len(entries) == 1:
            Log.detail("only one video; skipping the cross-video roll-up")
        elif entries:
            Log.detail("nothing new to synthesize; keeping the existing index.md")

    # ── Summary ──────────────────────────────────────────────────────
    Log.say("=" * 60)
    if args.transcribe_only:
        Log.say(f"  Done: {transcribed} transcript(s), {len(failures)} failure(s)")
    else:
        Log.say(f"  Done: {len(entries)} report(s), {len(failures)} failure(s)")
    if skipped:
        Log.say(f"  Skipped (already analyzed): {skipped}")
    Log.say("=" * 60)
    Log.say()
    if provider and provider.calls:
        used = provider.prompt_tokens + provider.completion_tokens
        Log.say(
            f"  LLM calls: {provider.calls}"
            + (f" · tokens reported: {used:,}" if used else "")
        )
    if rollup_path.is_file():
        Log.say(f"  Start here:  {rollup_path}")
    if not args.transcribe_only:
        Log.say(f"  Reports:     {videos_dir}/")
    Log.say(f"  Transcripts: {transcripts_dir}/")
    Log.say()
    if failures:
        Log.say("  Failures:")
        for name, reason in failures:
            Log.say(f"    - {name}: {reason}")
        Log.say()
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted. Re-run the same command to resume.", file=sys.stderr)
        sys.exit(130)

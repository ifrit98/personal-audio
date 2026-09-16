#!/usr/bin/env python3
"""Discord front end for digest.sh.

Post a message containing YouTube links (or a playlist URL, or attach a
csv/json/yaml/txt list) and the bot transcribes and analyzes each video, then
hands the markdown back as file attachments, a secret GitHub gist, or both.

Because a run costs real money and CPU time, the bot refuses to start without
an allowlist of users or channels.

Run with ./bot.sh. See README for the Discord application setup.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shlex
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import digest  # noqa: E402  (stdlib-only local module)

try:
    import discord
except ImportError:  # keeps this module importable for tests
    discord = None  # type: ignore[assignment]

DISCORD_MESSAGE_LIMIT = 2000
GIST_API = "https://api.github.com/gists"

# Bare words that toggle behaviour, versus key=value options.
FLAG_OPTIONS = {"safe", "chunk", "force", "quiet", "dryrun", "dry-run"}
DELIVERY_MODES = {"files", "gist", "both"}


# ──────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────


def env_ids(name: str) -> set[int]:
    raw = os.environ.get(name, "")
    ids: set[int] = set()
    for piece in re.split(r"[,\s]+", raw):
        piece = piece.strip()
        if piece.isdigit():
            ids.add(int(piece))
    return ids


@dataclass
class Config:
    token: str
    allowed_users: set[int] = field(default_factory=set)
    allowed_channels: set[int] = field(default_factory=set)
    allowed_guilds: set[int] = field(default_factory=set)
    delivery: str = "files"
    github_token: str = ""
    gist_public: bool = False
    whisper_model: str = "base.en"
    threads: int = 4
    provider: str = ""
    llm_model: str = ""
    max_videos: int = 25
    output_root: Path = SCRIPT_DIR / "digests"
    upload_limit_bytes: int = 8 * 1024 * 1024
    max_attachments: int = 9
    progress_interval: float = 6.0

    @classmethod
    def from_env(cls) -> Config:
        token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        if not token:
            raise SystemExit(
                "DISCORD_BOT_TOKEN is not set. Add it to .env — see the "
                "Discord Bot section of the README."
            )

        delivery = os.environ.get("DIGEST_DELIVERY", "files").strip().lower()
        if delivery not in DELIVERY_MODES:
            raise SystemExit(
                f"DIGEST_DELIVERY must be one of {sorted(DELIVERY_MODES)}, "
                f"got {delivery!r}"
            )

        config = cls(
            token=token,
            allowed_users=env_ids("DISCORD_ALLOWED_USERS"),
            allowed_channels=env_ids("DISCORD_ALLOWED_CHANNELS"),
            allowed_guilds=env_ids("DISCORD_ALLOWED_GUILDS"),
            delivery=delivery,
            github_token=os.environ.get("GITHUB_TOKEN", "").strip(),
            gist_public=os.environ.get("DIGEST_GIST_PUBLIC", "").lower()
            in ("1", "true", "yes"),
            whisper_model=os.environ.get("DIGEST_WHISPER_MODEL", "base.en"),
            threads=int(os.environ.get("DIGEST_THREADS", "4")),
            provider=os.environ.get("DIGEST_PROVIDER", "").strip(),
            llm_model=os.environ.get("DIGEST_MODEL", "").strip(),
            max_videos=int(os.environ.get("DIGEST_MAX_VIDEOS", "25")),
            output_root=Path(
                os.environ.get("DIGEST_OUTPUT_ROOT", str(SCRIPT_DIR / "digests"))
            ).expanduser(),
        )

        # An open bot is an open invoice: anyone who can see the channel could
        # queue hours of transcription and paid API calls.
        if not (config.allowed_users or config.allowed_channels
                or config.allowed_guilds):
            raise SystemExit(
                "Refusing to start without an allowlist. Set at least one of "
                "DISCORD_ALLOWED_USERS, DISCORD_ALLOWED_CHANNELS, or "
                "DISCORD_ALLOWED_GUILDS in .env."
            )
        if delivery in ("gist", "both") and not config.github_token:
            raise SystemExit(
                f"DIGEST_DELIVERY={delivery} needs GITHUB_TOKEN (a token with "
                "the 'gist' scope) in .env."
            )
        return config

    def authorized(self, user_id: int, channel_id: int,
                   guild_id: int | None) -> bool:
        if user_id in self.allowed_users:
            return True
        if channel_id in self.allowed_channels:
            return True
        if guild_id is not None and guild_id in self.allowed_guilds:
            return True
        return False


# ──────────────────────────────────────────────────────────────────────────
# Request parsing
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class Request:
    """What the user asked for, extracted from a Discord message."""

    urls: list[str] = field(default_factory=list)
    title: str = ""
    limit: int | None = None
    whisper_model: str = ""
    llm_model: str = ""
    provider: str = ""
    threads: int | None = None
    delivery: str = ""
    flags: set[str] = field(default_factory=set)
    unknown: list[str] = field(default_factory=list)


def parse_request(text: str) -> Request:
    """Pull URLs and `key=value` options out of free-form message text.

    URLs are removed before option parsing so query strings such as `?list=`
    are never mistaken for options.
    """
    request = Request()
    if not text:
        return request

    # Discord wraps pasted links in <> to suppress embeds; strip that.
    cleaned = text.replace("<", " ").replace(">", " ")
    cleaned = re.sub(r"<@!?\d+>", " ", cleaned)

    for match in digest.URL_IN_TEXT_RE.finditer(cleaned):
        url = match.group(0).rstrip(".,;)")
        if url not in request.urls:
            request.urls.append(url)

    remainder = digest.URL_IN_TEXT_RE.sub(" ", cleaned)
    remainder = re.sub(r"^\s*!?digest\b", " ", remainder, flags=re.IGNORECASE)

    try:
        tokens = shlex.split(remainder)
    except ValueError:
        tokens = remainder.split()

    for token in tokens:
        lowered = token.lower().strip("-")
        if lowered in FLAG_OPTIONS:
            request.flags.add(lowered.replace("dry-run", "dryrun"))
            continue
        if "=" not in token and ":" not in token:
            continue

        separator = "=" if "=" in token else ":"
        key, _, value = token.partition(separator)
        key = key.lower().strip("-")
        value = value.strip()
        if not value:
            continue

        if key == "limit" and value.isdigit():
            request.limit = int(value)
        elif key == "threads" and value.isdigit():
            request.threads = int(value)
        elif key in ("whisper", "model", "m"):
            request.whisper_model = value
        elif key in ("llm", "llm-model", "llmmodel"):
            request.llm_model = value
        elif key == "provider":
            request.provider = value.lower()
        elif key == "title":
            request.title = value
        elif key in ("as", "delivery", "deliver"):
            if value.lower() in DELIVERY_MODES:
                request.delivery = value.lower()
        else:
            request.unknown.append(token)

    return request


def default_title(author: str) -> str:
    return f"Discord {author} {datetime.now():%Y-%m-%d %H%M%S}"


def build_command(request: Request, config: Config, list_file: Path | None,
                  title: str, output_root: Path) -> list[str]:
    """Assemble the digest.py invocation for a job."""
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "digest.py"),
        "--title", title,
        "-o", str(output_root),
        "-m", request.whisper_model or config.whisper_model,
        "-t", str(request.threads or config.threads),
    ]

    limit = request.limit or config.max_videos
    cmd += ["--limit", str(min(limit, config.max_videos))]

    provider = request.provider or config.provider
    if provider:
        cmd += ["--provider", provider]
    llm_model = request.llm_model or config.llm_model
    if llm_model:
        cmd += ["--llm-model", llm_model]

    if "safe" in request.flags:
        cmd.append("-s")
    if "chunk" in request.flags:
        cmd.append("--chunk")
    if "force" in request.flags:
        cmd.append("--force")
    if "dryrun" in request.flags:
        cmd.append("--dry-run")

    if list_file is not None:
        cmd.append(str(list_file))
    else:
        cmd += request.urls
    return cmd


# ──────────────────────────────────────────────────────────────────────────
# Running digest.py
# ──────────────────────────────────────────────────────────────────────────

ProgressHook = Callable[[str], Awaitable[None]]


async def run_digest(cmd: Sequence[str], on_progress: ProgressHook | None = None,
                     ) -> tuple[int, list[str]]:
    """Run digest.py, streaming its output so progress can be reported live."""
    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(SCRIPT_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    lines: list[str] = []
    assert process.stdout is not None
    async for raw in process.stdout:
        line = raw.decode("utf-8", errors="replace").rstrip()
        if not line.strip():
            continue
        lines.append(line)
        if on_progress:
            await on_progress(line)

    await process.wait()
    return process.returncode or 0, lines


def interesting_progress(lines: Sequence[str], keep: int = 6) -> str:
    """The tail of digest.py's output, for a live status message."""
    noise = ("=" * 10, "-" * 10)
    useful = [
        line for line in lines
        if line.strip() and not line.strip().startswith(noise)
    ]
    return "\n".join(useful[-keep:])


# ──────────────────────────────────────────────────────────────────────────
# Collecting and packaging results
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class Results:
    run_dir: Path
    index: Path | None
    reports: list[Path]

    @property
    def all_files(self) -> list[Path]:
        return ([self.index] if self.index else []) + self.reports

    @property
    def total_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.all_files if p.is_file())


def collect_results(run_dir: Path) -> Results:
    index = run_dir / "index.md"
    reports = sorted((run_dir / "videos").glob("*.md")) if (
        run_dir / "videos").is_dir() else []
    return Results(run_dir, index if index.is_file() else None, reports)


def zip_results(results: Results, name: str) -> tuple[str, bytes]:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in results.all_files:
            archive.write(path, arcname=path.relative_to(results.run_dir))
    return f"{name}.zip", buffer.getvalue()


def preview_text(results: Results, limit: int = 1200) -> str:
    """A readable excerpt for posting inline, taken from the roll-up."""
    source = results.index or (results.reports[0] if results.reports else None)
    if source is None:
        return ""

    body = source.read_text(encoding="utf-8", errors="replace")
    body = re.sub(r"^---\n.*?\n---\n", "", body, count=1, flags=re.DOTALL)
    # Skip the title and byline down to the first real section.
    first_section = body.find("\n## ")
    if first_section != -1:
        body = body[first_section + 1:]
    body = body.strip()

    if len(body) <= limit:
        return body
    cut = body[:limit]
    boundary = max(cut.rfind("\n\n"), cut.rfind(". "))
    if boundary > limit // 2:
        cut = cut[: boundary + 1]
    return cut.rstrip() + "\n\n…"


def unverified_quote_total(results: Results) -> int:
    total = 0
    for path in results.reports:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[:20]:
            match = re.match(r"unverified_quotes:\s*(\d+)", line.strip())
            if match:
                total += int(match.group(1))
                break
    return total


# ──────────────────────────────────────────────────────────────────────────
# Gist delivery
# ──────────────────────────────────────────────────────────────────────────

GIST_FILE_LIMIT = 900_000


def gist_payload(files: Sequence[Path], run_dir: Path, description: str,
                 public: bool = False) -> dict[str, Any]:
    """Build the GitHub gist request body from report files."""
    payload_files: dict[str, dict[str, str]] = {}
    for path in files:
        # Gist filenames are flat, so encode the folder into the name.
        relative = path.relative_to(run_dir)
        name = str(relative).replace("/", "__")
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > GIST_FILE_LIMIT:
            content = content[:GIST_FILE_LIMIT] + "\n\n_[truncated for gist]_\n"
        if content.strip():
            payload_files[name] = {"content": content}
    return {
        "description": description,
        "public": public,
        "files": payload_files,
    }


def create_gist(token: str, payload: dict[str, Any], timeout: int = 60) -> str:
    """Create a gist and return its URL. Blocking; call via asyncio.to_thread."""
    request = urllib.request.Request(
        GIST_API,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "audio-digest-bot",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response).get("html_url", "")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(body).get("message", body)
        except json.JSONDecodeError:
            message = body
        raise RuntimeError(f"gist upload failed (HTTP {exc.code}): "
                           f"{str(message)[:200]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"gist upload failed: {exc.reason}") from exc


# ──────────────────────────────────────────────────────────────────────────
# Bot
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class Job:
    request: Request
    title: str
    list_file: Path | None
    author: str
    channel: Any
    status_message: Any
    delivery: str


HELP_TEXT = """\
**Send me YouTube links and I'll analyze them.**

Post a playlist URL, one or more video URLs, or attach a `.txt`/`.csv`/`.json`/
`.yaml` list. I transcribe each video with whisper.cpp and return a report
covering the major themes, each speaker's claims, a steel-manned read of the
conclusions, the supporting evidence, and a critical assessment.

**Options** (append anywhere in the message)
`limit=5` — cap the number of videos
`whisper=small.en` — better transcription, slower
`llm=gpt-5.6-sol` — use a stronger model
`provider=anthropic` — openai, anthropic, openrouter, or lmstudio
`title="Macro Week 37"` — name the run
`as=gist` — return a gist link instead of files (`files`, `gist`, or `both`)
`safe` — rate-limit downloads
`force` — redo videos already analyzed
`dryrun` — just list what would be processed, costing nothing

**Example**
`https://youtube.com/playlist?list=PLxxx limit=3 whisper=small.en as=gist`
"""


def build_bot(config: Config):  # noqa: C901  (event handlers read better inline)
    if discord is None:
        raise SystemExit(
            "discord.py is not installed. Run ./bot.sh, which sets up the "
            "virtualenv, or: python3 -m pip install -r requirements.txt"
        )

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    queue: asyncio.Queue[Job] = asyncio.Queue()

    async def reply(channel: Any, content: str, **kwargs: Any) -> Any:
        if len(content) > DISCORD_MESSAGE_LIMIT:
            content = content[: DISCORD_MESSAGE_LIMIT - 20].rstrip() + "\n…"
        return await channel.send(content, **kwargs)

    # ── Job execution ────────────────────────────────────────────────
    async def deliver(job: Job, results: Results) -> None:
        channel = job.channel
        lines = [f"**{job.title}** — done."]
        if results.index:
            lines.append(f"{len(results.reports)} report(s) plus a cross-video index.")
        else:
            lines.append(f"{len(results.reports)} report(s).")

        flagged = unverified_quote_total(results)
        if flagged:
            lines.append(
                f":warning: {flagged} quote(s) could not be matched to the "
                "transcript and are flagged inline."
            )

        want_files = job.delivery in ("files", "both")
        if job.delivery in ("gist", "both"):
            try:
                payload = gist_payload(
                    results.all_files, results.run_dir,
                    f"{job.title} — audio digest", config.gist_public,
                )
                url = await asyncio.to_thread(
                    create_gist, config.github_token, payload
                )
                lines.append(f"Gist: {url}")
            except RuntimeError as exc:
                # A failed gist should still hand the reports over somehow.
                lines.append(f":warning: {exc}")
                want_files = True

        files: list[Any] = []
        if want_files:
            slug = digest.slugify(job.title)
            too_many = len(results.all_files) > config.max_attachments
            too_big = results.total_bytes > config.upload_limit_bytes
            if too_many or too_big:
                name, blob = await asyncio.to_thread(zip_results, results, slug)
                if len(blob) <= config.upload_limit_bytes:
                    files.append(discord.File(io.BytesIO(blob), filename=name))
                else:
                    lines.append(
                        ":warning: results are too large to upload; they are on "
                        f"disk at `{results.run_dir}`"
                    )
            else:
                for path in results.all_files:
                    files.append(discord.File(str(path), filename=path.name))

        preview = preview_text(results)
        body = "\n".join(lines)
        if preview:
            room = DISCORD_MESSAGE_LIMIT - len(body) - 20
            if room > 300:
                body += f"\n\n>>> {preview[:room]}"

        if files:
            await reply(channel, body, files=files)
        else:
            await reply(channel, body)

    async def process(job: Job) -> None:
        output_root = config.output_root
        cmd = build_command(job.request, config, job.list_file, job.title,
                            output_root)
        run_dir = output_root / digest.slugify(job.title)

        lines: list[str] = []
        last_edit = 0.0

        async def on_progress(line: str) -> None:
            nonlocal last_edit
            now = time.monotonic()
            if now - last_edit < config.progress_interval:
                return
            last_edit = now
            tail = interesting_progress(lines)
            try:
                await job.status_message.edit(
                    content=f"**{job.title}** — working…\n```\n{tail[:1800]}\n```"
                )
            except Exception:
                pass  # a failed status edit must never kill the job

        code, lines = await run_digest(cmd, on_progress)

        if "dryrun" in job.request.flags:
            tail = interesting_progress(lines, keep=40)
            await job.status_message.edit(
                content=f"**{job.title}** — dry run\n```\n{tail[:1800]}\n```"
            )
            return

        results = collect_results(run_dir)
        if not results.all_files:
            tail = interesting_progress(lines, keep=12)
            await job.status_message.edit(
                content=(
                    f"**{job.title}** — failed, no reports produced "
                    f"(exit {code}).\n```\n{tail[:1700]}\n```"
                )
            )
            return

        await job.status_message.edit(
            content=f"**{job.title}** — analysis complete, uploading…"
        )
        await deliver(job, results)
        if code != 0:
            await reply(
                job.channel,
                ":warning: some videos failed; see the run log on the host.",
            )

    async def worker() -> None:
        while True:
            job = await queue.get()
            try:
                await process(job)
            except Exception as exc:  # a bad job must not kill the worker
                try:
                    await reply(job.channel, f":x: Job failed: `{exc}`")
                except Exception:
                    pass
                print(f"[bot] job error: {exc!r}", file=sys.stderr, flush=True)
            finally:
                queue.task_done()

    # ── Events ───────────────────────────────────────────────────────
    @client.event
    async def on_ready() -> None:
        # on_ready fires again after a reconnect, so only ever start one worker.
        if not getattr(client, "_digest_worker_started", False):
            client._digest_worker_started = True  # type: ignore[attr-defined]
            asyncio.create_task(worker())
        print(f"[bot] connected as {client.user}", flush=True)
        print(f"[bot] delivery={config.delivery} "
              f"whisper={config.whisper_model} "
              f"max_videos={config.max_videos}", flush=True)

    @client.event
    async def on_message(message: Any) -> None:
        if message.author.bot:
            return

        is_dm = message.guild is None
        mentioned = client.user in getattr(message, "mentions", [])
        watched = message.channel.id in config.allowed_channels
        parent_watched = getattr(message.channel, "parent_id", None) in \
            config.allowed_channels
        if not (is_dm or mentioned or watched or parent_watched):
            return

        if not config.authorized(
            message.author.id, message.channel.id,
            message.guild.id if message.guild else None,
        ):
            await reply(
                message.channel,
                ":no_entry: Not authorized. Ask the host to add your user ID "
                f"(`{message.author.id}`) to `DISCORD_ALLOWED_USERS`.",
            )
            return

        request = parse_request(message.content or "")

        # Attached list files travel with the message.
        list_file: Path | None = None
        for attachment in getattr(message, "attachments", []):
            suffix = Path(attachment.filename).suffix.lower()
            if suffix in (".txt", ".csv", ".tsv", ".json", ".yaml", ".yml"):
                inbox = digest.ensure_dir(SCRIPT_DIR / "digests" / "_inbox")
                list_file = inbox / f"{int(time.time())}-{attachment.filename}"
                list_file.write_bytes(await attachment.read())
                break

        if not request.urls and list_file is None:
            if message.content and re.search(r"\bhelp\b", message.content, re.I):
                await reply(message.channel, HELP_TEXT)
            elif is_dm or mentioned:
                await reply(
                    message.channel,
                    "I did not find any links. Say `help` for usage.",
                )
            return

        title = request.title or default_title(message.author.display_name)
        delivery = request.delivery or config.delivery
        if delivery in ("gist", "both") and not config.github_token:
            delivery = "files"

        count = len(request.urls) if list_file is None else 0
        source = (
            f"{count} link(s)" if count else f"list `{list_file.name}`"
        )
        if request.unknown:
            source += f" (ignored: {', '.join(request.unknown[:4])})"

        status = await reply(
            message.channel,
            f"**{title}** — queued ({source}, position {queue.qsize() + 1}).\n"
            "Transcription takes a few minutes per video; I'll update here.",
        )

        await queue.put(
            Job(
                request=request,
                title=title,
                list_file=list_file,
                author=message.author.display_name,
                channel=message.channel,
                status_message=status,
                delivery=delivery,
            )
        )

    return client


def main() -> int:
    digest.load_env_file(SCRIPT_DIR / ".env")
    config = Config.from_env()
    client = build_bot(config)
    client.run(config.token, log_handler=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())

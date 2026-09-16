import io
import os
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

import discord_bot as bot


# ──────────────────────────────────────────────────────────────────────────
# env_ids
# ──────────────────────────────────────────────────────────────────────────


class TestEnvIds(unittest.TestCase):
    def test_missing_var_is_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(bot.env_ids("NOPE"), set())

    def test_comma_and_whitespace_separated(self):
        with patch.dict(os.environ, {"IDS": "1, 2  3,4"}, clear=True):
            self.assertEqual(bot.env_ids("IDS"), {1, 2, 3, 4})

    def test_non_digit_pieces_are_dropped(self):
        with patch.dict(os.environ, {"IDS": "1,abc,2,"}, clear=True):
            self.assertEqual(bot.env_ids("IDS"), {1, 2})


# ──────────────────────────────────────────────────────────────────────────
# Config.from_env / Config.authorized
# ──────────────────────────────────────────────────────────────────────────


class TestConfigFromEnv(unittest.TestCase):
    def _env(self, **overrides):
        env = {
            "DISCORD_BOT_TOKEN": "tok",
            "DISCORD_ALLOWED_USERS": "111",
        }
        env.update(overrides)
        return env

    def test_missing_token_exits(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                bot.Config.from_env()
        self.assertIn("DISCORD_BOT_TOKEN", str(ctx.exception))

    def test_no_allowlist_exits(self):
        env = {"DISCORD_BOT_TOKEN": "tok"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                bot.Config.from_env()
        self.assertIn("allowlist", str(ctx.exception))

    def test_bad_delivery_mode_exits(self):
        with patch.dict(os.environ, self._env(DIGEST_DELIVERY="carrier-pigeon"),
                        clear=True):
            with self.assertRaises(SystemExit) as ctx:
                bot.Config.from_env()
        self.assertIn("DIGEST_DELIVERY", str(ctx.exception))

    def test_gist_delivery_without_github_token_exits(self):
        with patch.dict(os.environ, self._env(DIGEST_DELIVERY="gist"), clear=True):
            with self.assertRaises(SystemExit) as ctx:
                bot.Config.from_env()
        self.assertIn("GITHUB_TOKEN", str(ctx.exception))

    def test_gist_delivery_with_github_token_succeeds(self):
        env = self._env(DIGEST_DELIVERY="both", GITHUB_TOKEN="ghp_x")
        with patch.dict(os.environ, env, clear=True):
            config = bot.Config.from_env()
        self.assertEqual(config.delivery, "both")
        self.assertEqual(config.github_token, "ghp_x")

    def test_defaults_and_parsed_values(self):
        env = self._env(
            DISCORD_ALLOWED_CHANNELS="222,333",
            DIGEST_MAX_VIDEOS="7",
            DIGEST_THREADS="2",
        )
        with patch.dict(os.environ, env, clear=True):
            config = bot.Config.from_env()
        self.assertEqual(config.token, "tok")
        self.assertEqual(config.allowed_users, {111})
        self.assertEqual(config.allowed_channels, {222, 333})
        self.assertEqual(config.delivery, "files")
        self.assertEqual(config.max_videos, 7)
        self.assertEqual(config.threads, 2)
        self.assertEqual(config.whisper_model, "base.en")


class TestConfigAuthorized(unittest.TestCase):
    def test_authorized_by_user(self):
        config = bot.Config(token="t", allowed_users={1})
        self.assertTrue(config.authorized(1, 999, None))
        self.assertFalse(config.authorized(2, 999, None))

    def test_authorized_by_channel(self):
        config = bot.Config(token="t", allowed_channels={5})
        self.assertTrue(config.authorized(1, 5, None))

    def test_authorized_by_guild(self):
        config = bot.Config(token="t", allowed_guilds={9})
        self.assertTrue(config.authorized(1, 2, 9))
        self.assertFalse(config.authorized(1, 2, None))

    def test_unauthorized_when_nothing_matches(self):
        config = bot.Config(token="t", allowed_users={1}, allowed_channels={5},
                            allowed_guilds={9})
        self.assertFalse(config.authorized(2, 6, 10))


# ──────────────────────────────────────────────────────────────────────────
# parse_request
# ──────────────────────────────────────────────────────────────────────────


class TestParseRequest(unittest.TestCase):
    def test_empty_text(self):
        request = bot.parse_request("")
        self.assertEqual(request.urls, [])
        self.assertEqual(request.flags, set())

    def test_extracts_and_dedupes_urls(self):
        text = "check https://youtu.be/abc and https://youtu.be/abc again"
        request = bot.parse_request(text)
        self.assertEqual(request.urls, ["https://youtu.be/abc"])

    def test_strips_discord_link_brackets(self):
        request = bot.parse_request("<https://youtu.be/abc> limit=2")
        self.assertEqual(request.urls, ["https://youtu.be/abc"])
        self.assertEqual(request.limit, 2)

    def test_strips_mentions(self):
        request = bot.parse_request("<@123456> https://youtu.be/abc")
        self.assertEqual(request.urls, ["https://youtu.be/abc"])

    def test_strips_leading_digest_command(self):
        request = bot.parse_request("!digest https://youtu.be/abc limit=1")
        self.assertEqual(request.urls, ["https://youtu.be/abc"])
        self.assertEqual(request.limit, 1)

    def test_bare_flags(self):
        request = bot.parse_request("https://youtu.be/abc safe force --dry-run")
        self.assertEqual(request.flags, {"safe", "force", "dryrun"})

    def test_dryrun_and_dry_dash_run_are_equivalent(self):
        self.assertEqual(bot.parse_request("dryrun").flags, {"dryrun"})
        self.assertEqual(bot.parse_request("dry-run").flags, {"dryrun"})
        self.assertEqual(bot.parse_request("--dry-run").flags, {"dryrun"})

    def test_key_value_options(self):
        request = bot.parse_request(
            "https://youtu.be/abc limit=3 threads=8 whisper=small.en "
            "llm=gpt-5.6-sol provider=OpenAI title=\"Macro Week 37\" as=gist"
        )
        self.assertEqual(request.limit, 3)
        self.assertEqual(request.threads, 8)
        self.assertEqual(request.whisper_model, "small.en")
        self.assertEqual(request.llm_model, "gpt-5.6-sol")
        self.assertEqual(request.provider, "openai")
        self.assertEqual(request.title, "Macro Week 37")
        self.assertEqual(request.delivery, "gist")

    def test_colon_separator_also_works(self):
        request = bot.parse_request("https://youtu.be/abc limit:5")
        self.assertEqual(request.limit, 5)

    def test_key_aliases(self):
        self.assertEqual(bot.parse_request("model=tiny.en").whisper_model, "tiny.en")
        self.assertEqual(bot.parse_request("m=tiny.en").whisper_model, "tiny.en")
        self.assertEqual(
            bot.parse_request("llm-model=gpt-5.6-sol").llm_model, "gpt-5.6-sol"
        )
        self.assertEqual(bot.parse_request("deliver=both").delivery, "both")

    def test_invalid_delivery_value_is_silently_ignored(self):
        request = bot.parse_request("as=carrier-pigeon")
        self.assertEqual(request.delivery, "")

    def test_non_digit_limit_is_ignored(self):
        request = bot.parse_request("limit=many")
        self.assertIsNone(request.limit)

    def test_unknown_key_value_is_recorded(self):
        request = bot.parse_request("foo=bar")
        self.assertEqual(request.unknown, ["foo=bar"])

    def test_bare_word_without_separator_is_ignored(self):
        request = bot.parse_request("hello there")
        self.assertEqual(request.unknown, [])
        self.assertEqual(request.flags, set())


class TestDefaultTitle(unittest.TestCase):
    def test_includes_author_name(self):
        title = bot.default_title("Jason")
        self.assertTrue(title.startswith("Discord Jason "))


# ──────────────────────────────────────────────────────────────────────────
# build_command
# ──────────────────────────────────────────────────────────────────────────


class TestBuildCommand(unittest.TestCase):
    def setUp(self):
        self.config = bot.Config(
            token="t", allowed_users={1}, whisper_model="base.en", threads=4,
            max_videos=25,
        )

    def test_uses_config_defaults_when_request_is_empty(self):
        request = bot.Request(urls=["https://youtu.be/abc"])
        cmd = bot.build_command(request, self.config, None, "My Title",
                                Path("/out"))
        self.assertIn("--title", cmd)
        self.assertEqual(cmd[cmd.index("--title") + 1], "My Title")
        self.assertEqual(cmd[cmd.index("-o") + 1], "/out")
        self.assertEqual(cmd[cmd.index("-m") + 1], "base.en")
        self.assertEqual(cmd[cmd.index("-t") + 1], "4")
        self.assertEqual(cmd[cmd.index("--limit") + 1], "25")
        self.assertTrue(cmd[-1].endswith("abc"))

    def test_request_overrides_config(self):
        request = bot.Request(
            urls=["https://youtu.be/abc"], whisper_model="small.en", threads=8,
            provider="anthropic", llm_model="claude-sonnet-4-5",
        )
        cmd = bot.build_command(request, self.config, None, "T", Path("/out"))
        self.assertEqual(cmd[cmd.index("-m") + 1], "small.en")
        self.assertEqual(cmd[cmd.index("-t") + 1], "8")
        self.assertEqual(cmd[cmd.index("--provider") + 1], "anthropic")
        self.assertEqual(cmd[cmd.index("--llm-model") + 1], "claude-sonnet-4-5")

    def test_limit_is_capped_to_max_videos(self):
        request = bot.Request(urls=["https://youtu.be/abc"], limit=999)
        cmd = bot.build_command(request, self.config, None, "T", Path("/out"))
        self.assertEqual(cmd[cmd.index("--limit") + 1], "25")

    def test_limit_under_cap_is_kept(self):
        request = bot.Request(urls=["https://youtu.be/abc"], limit=3)
        cmd = bot.build_command(request, self.config, None, "T", Path("/out"))
        self.assertEqual(cmd[cmd.index("--limit") + 1], "3")

    def test_flags_map_to_switches(self):
        request = bot.Request(
            urls=["https://youtu.be/abc"],
            flags={"safe", "chunk", "force", "dryrun"},
        )
        cmd = bot.build_command(request, self.config, None, "T", Path("/out"))
        for switch in ("-s", "--chunk", "--force", "--dry-run"):
            self.assertIn(switch, cmd)

    def test_list_file_replaces_urls(self):
        request = bot.Request(urls=["https://youtu.be/abc"])
        cmd = bot.build_command(request, self.config, Path("/tmp/list.txt"),
                                "T", Path("/out"))
        self.assertNotIn("https://youtu.be/abc", cmd)
        self.assertEqual(cmd[-1], "/tmp/list.txt")


# ──────────────────────────────────────────────────────────────────────────
# interesting_progress
# ──────────────────────────────────────────────────────────────────────────


class TestInterestingProgress(unittest.TestCase):
    def test_drops_blank_lines_and_separators(self):
        lines = ["", "=" * 20, "step one", "-" * 20, "step two", "   "]
        self.assertEqual(bot.interesting_progress(lines), "step one\nstep two")

    def test_keeps_only_the_tail(self):
        lines = [f"line {i}" for i in range(10)]
        result = bot.interesting_progress(lines, keep=3)
        self.assertEqual(result, "line 7\nline 8\nline 9")


# ──────────────────────────────────────────────────────────────────────────
# Results / collect_results / zip_results / preview_text / unverified quotes
# ──────────────────────────────────────────────────────────────────────────


class TestResultsHelpers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name) / "run"
        (self.run_dir / "videos").mkdir(parents=True)
        (self.run_dir / "index.md").write_text(
            "---\ntitle: x\n---\n# Title\n\n## Overview\nHello world.\n"
        )
        (self.run_dir / "videos" / "a.md").write_text(
            "unverified_quotes: 2\n# A\nBody A\n"
        )
        (self.run_dir / "videos" / "b.md").write_text(
            "unverified_quotes: 1\n# B\nBody B\n"
        )

    def test_collect_results_finds_index_and_reports(self):
        results = bot.collect_results(self.run_dir)
        self.assertEqual(results.index, self.run_dir / "index.md")
        self.assertEqual(
            results.reports,
            [self.run_dir / "videos" / "a.md", self.run_dir / "videos" / "b.md"],
        )
        self.assertEqual(len(results.all_files), 3)

    def test_collect_results_without_index(self):
        (self.run_dir / "index.md").unlink()
        results = bot.collect_results(self.run_dir)
        self.assertIsNone(results.index)
        self.assertEqual(len(results.all_files), 2)

    def test_collect_results_without_videos_dir(self):
        import shutil
        shutil.rmtree(self.run_dir / "videos")
        results = bot.collect_results(self.run_dir)
        self.assertEqual(results.reports, [])

    def test_zip_results_contains_relative_arcnames(self):
        results = bot.collect_results(self.run_dir)
        name, blob = bot.zip_results(results, "myslug")
        self.assertEqual(name, "myslug.zip")
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            names = set(archive.namelist())
        self.assertEqual(
            names,
            {"index.md", "videos/a.md", "videos/b.md"},
        )

    def test_preview_text_strips_frontmatter_and_title(self):
        results = bot.collect_results(self.run_dir)
        preview = bot.preview_text(results)
        self.assertNotIn("---", preview)
        self.assertNotIn("# Title", preview)
        self.assertIn("Hello world.", preview)

    def test_preview_text_falls_back_to_first_report(self):
        (self.run_dir / "index.md").unlink()
        results = bot.collect_results(self.run_dir)
        preview = bot.preview_text(results)
        self.assertIn("Body A", preview)

    def test_preview_text_empty_when_no_source(self):
        results = bot.Results(self.run_dir, None, [])
        self.assertEqual(bot.preview_text(results), "")

    def test_preview_text_truncates_long_body(self):
        long_body = "Sentence one. " * 500
        (self.run_dir / "index.md").write_text(f"# T\n\n## S\n{long_body}")
        results = bot.collect_results(self.run_dir)
        preview = bot.preview_text(results, limit=100)
        self.assertLessEqual(len(preview), 130)
        self.assertTrue(preview.endswith("…"))

    def test_unverified_quote_total_sums_reports(self):
        results = bot.collect_results(self.run_dir)
        self.assertEqual(bot.unverified_quote_total(results), 3)


# ──────────────────────────────────────────────────────────────────────────
# gist_payload / create_gist
# ──────────────────────────────────────────────────────────────────────────


class TestGistPayload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name)
        (self.run_dir / "videos").mkdir()

    def test_flattens_subdirectory_filenames(self):
        path = self.run_dir / "videos" / "a.md"
        path.write_text("content")
        payload = bot.gist_payload([path], self.run_dir, "desc")
        self.assertEqual(payload["description"], "desc")
        self.assertFalse(payload["public"])
        self.assertIn("videos__a.md", payload["files"])
        self.assertEqual(payload["files"]["videos__a.md"]["content"], "content")

    def test_skips_blank_files(self):
        path = self.run_dir / "empty.md"
        path.write_text("   \n")
        payload = bot.gist_payload([path], self.run_dir, "desc")
        self.assertEqual(payload["files"], {})

    def test_truncates_oversized_content(self):
        path = self.run_dir / "big.md"
        path.write_text("x" * (bot.GIST_FILE_LIMIT + 10))
        payload = bot.gist_payload([path], self.run_dir, "desc")
        content = payload["files"]["big.md"]["content"]
        self.assertLessEqual(len(content), bot.GIST_FILE_LIMIT + 100)
        self.assertIn("truncated for gist", content)


class TestCreateGist(unittest.TestCase):
    def test_returns_html_url_on_success(self):
        response_body = b'{"html_url": "https://gist.github.com/abc"}'

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return response_body

        with patch.object(bot.urllib.request, "urlopen",
                          return_value=FakeResponse()):
            url = bot.create_gist("tok", {"description": "d", "files": {}})
        self.assertEqual(url, "https://gist.github.com/abc")

    def test_http_error_raises_runtime_error_with_message(self):
        error = urllib.error.HTTPError(
            bot.GIST_API, 422, "Unprocessable",
            hdrs=None, fp=io.BytesIO(b'{"message": "bad payload"}'),
        )
        self.addCleanup(error.close)
        with patch.object(bot.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(RuntimeError) as ctx:
                bot.create_gist("tok", {"description": "d", "files": {}})
        self.assertIn("bad payload", str(ctx.exception))

    def test_url_error_raises_runtime_error(self):
        error = urllib.error.URLError("connection refused")
        with patch.object(bot.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(RuntimeError) as ctx:
                bot.create_gist("tok", {"description": "d", "files": {}})
        self.assertIn("connection refused", str(ctx.exception))


# ──────────────────────────────────────────────────────────────────────────
# run_digest
# ──────────────────────────────────────────────────────────────────────────


class FakeProcess:
    def __init__(self, output_lines, returncode=0):
        async def line_iter():
            for line in output_lines:
                yield line.encode("utf-8")

        self.stdout = line_iter()
        self._returncode = returncode
        self.returncode = None

    async def wait(self):
        self.returncode = self._returncode


class TestRunDigest(unittest.IsolatedAsyncioTestCase):
    async def test_streams_lines_and_calls_progress_hook(self):
        process = FakeProcess(["line one\n", "\n", "line two\n"])
        seen = []

        async def on_progress(line):
            seen.append(line)

        with patch.object(
            bot.asyncio, "create_subprocess_exec",
            return_value=process,
        ) as create:
            code, lines = await bot.run_digest(["digest.py"], on_progress)

        create.assert_awaited_once()
        self.assertEqual(code, 0)
        self.assertEqual(lines, ["line one", "line two"])
        self.assertEqual(seen, ["line one", "line two"])

    async def test_returns_nonzero_exit_code(self):
        process = FakeProcess(["boom\n"], returncode=1)
        with patch.object(bot.asyncio, "create_subprocess_exec",
                          return_value=process):
            code, lines = await bot.run_digest(["digest.py"])
        self.assertEqual(code, 1)
        self.assertEqual(lines, ["boom"])


if __name__ == "__main__":
    unittest.main()

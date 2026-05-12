import io
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

import aave


class TestEnvParser(unittest.TestCase):
    def test_parses_simple_kv(self):
        with TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("FOO=bar\n# comment\n\nBAZ=\"quoted value\"\n")
            env = aave._parse_env(str(p))
        self.assertEqual(env["FOO"], "bar")
        self.assertEqual(env["BAZ"], "quoted value")
        self.assertNotIn("# comment", env)

    def test_handles_missing_file(self):
        env = aave._parse_env("/nonexistent/path/.env")
        self.assertEqual(env, {})

    def test_does_not_overwrite_existing_env(self):
        with TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("MY_TEST_VAR=fromfile\n")
            os.environ["MY_TEST_VAR"] = "fromshell"
            try:
                aave._load_dotenv(str(p))
                self.assertEqual(os.environ["MY_TEST_VAR"], "fromshell")
            finally:
                del os.environ["MY_TEST_VAR"]


class TestSelftestCommand(unittest.TestCase):
    def test_selftest_passes(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = aave.cmd_selftest(args=None)
        self.assertEqual(rc, 0)
        self.assertIn("PASS", buf.getvalue())
        self.assertIn("keccak", buf.getvalue())


class TestArgparse(unittest.TestCase):
    # All three tests pass `env_path=None` so a stray .env in cwd doesn't
    # pollute os.environ for subsequent tests in the same process.

    def test_help_exits_zero(self):
        with self.assertRaises(SystemExit) as cm:
            aave.main(["--help"], env_path=None)
        self.assertEqual(cm.exception.code, 0)

    def test_unknown_subcommand_exits_nonzero(self):
        with self.assertRaises(SystemExit) as cm:
            aave.main(["unknown"], env_path=None)
        self.assertNotEqual(cm.exception.code, 0)

    def test_only_validates_market(self):
        with self.assertRaises(SystemExit) as cm:
            aave.main(["withdraw", "--only=BANANA"], env_path=None)
        self.assertNotEqual(cm.exception.code, 0)

    def test_main_env_path_none_does_not_pollute_environ(self):
        """Regression test for the test-isolation bug: when env_path=None,
        main() must NOT load a .env file even if one exists in cwd."""
        from pathlib import Path
        from tempfile import TemporaryDirectory
        sentinel = "AAVE_PY_TEST_ENV_LEAK_PROBE"
        # Make sure the sentinel isn't already in os.environ from a prior run.
        os.environ.pop(sentinel, None)
        with TemporaryDirectory() as d:
            envfile = Path(d) / ".env"
            envfile.write_text(f"{sentinel}=leaked\n")
            old_cwd = os.getcwd()
            try:
                os.chdir(d)
                with self.assertRaises(SystemExit):
                    aave.main(["--help"], env_path=None)
            finally:
                os.chdir(old_cwd)
        self.assertNotIn(sentinel, os.environ, "env_path=None should NOT load .env")

    def test_main_default_env_path_does_load_dotenv(self):
        """Confirm the default behavior (env_path='.env') still loads, so the
        regression fix doesn't accidentally break production behavior."""
        from pathlib import Path
        from tempfile import TemporaryDirectory
        sentinel = "AAVE_PY_TEST_ENV_LOAD_PROBE"
        os.environ.pop(sentinel, None)
        try:
            with TemporaryDirectory() as d:
                envfile = Path(d) / ".env"
                envfile.write_text(f"{sentinel}=loaded\n")
                old_cwd = os.getcwd()
                try:
                    os.chdir(d)
                    with self.assertRaises(SystemExit):
                        # default env_path=".env" should load the dotenv.
                        aave.main(["--help"])
                finally:
                    os.chdir(old_cwd)
            self.assertEqual(os.environ.get(sentinel), "loaded",
                             "default env_path should load .env into os.environ")
        finally:
            os.environ.pop(sentinel, None)


if __name__ == "__main__":
    unittest.main()

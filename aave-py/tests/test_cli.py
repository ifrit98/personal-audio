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
    def test_help_exits_zero(self):
        with self.assertRaises(SystemExit) as cm:
            aave.main(["--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_unknown_subcommand_exits_nonzero(self):
        with self.assertRaises(SystemExit) as cm:
            aave.main(["unknown"])
        self.assertNotEqual(cm.exception.code, 0)

    def test_only_validates_market(self):
        with self.assertRaises(SystemExit) as cm:
            aave.main(["withdraw", "--only=BANANA"])
        self.assertNotEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()

"""Aave V3 Stable Drainer (Python, minimal) — CLI entrypoint."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Make sibling `lib/` importable when running `python aave.py ...` directly.
sys.path.insert(0, str(Path(__file__).parent))

from lib import vectors  # noqa: E402


VERSION = "0.1.0"
VALID_MARKETS = ("DAI", "USDC", "USDT")


# ----------------------- .env parser -----------------------

def _parse_env(path: str) -> dict[str, str]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    out: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        # Strip a single matching pair of surrounding quotes.
        if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
            v = v[1:-1]
        out[k] = v
    return out


def _load_dotenv(path: str = ".env") -> None:
    """Set keys from .env into os.environ, but only if not already set."""
    for k, v in _parse_env(path).items():
        os.environ.setdefault(k, v)


# ----------------------- selftest -----------------------

def cmd_selftest(args: argparse.Namespace | None) -> int:
    ok, results = vectors.run_all()
    width = max(len(name) for name, _, _ in results)
    for name, passed, detail in results:
        status = "PASS" if passed else "FAIL"
        line = f"  {name.ljust(width)}  {status}"
        if detail:
            line += f"  -- {detail}"
        print(line)
    print()
    if ok:
        print("selftest: ALL PASS")
        return 0
    print("selftest: FAILED -- refusing to sign anything")
    return 3


def _ensure_selftest_passes() -> None:
    ok, results = vectors.run_all()
    if not ok:
        print("selftest failed; refusing to continue. Run `python aave.py selftest` for details.")
        for name, passed, detail in results:
            if not passed:
                print(f"  {name}: {detail}")
        sys.exit(3)


# ----------------------- verify (stub, Task 13 fills in) -----------------------

def cmd_verify(args: argparse.Namespace) -> int:
    _ensure_selftest_passes()
    print("verify: not yet implemented")
    return 0


# ----------------------- withdraw (stub, Task 14 fills in) -----------------------

def cmd_withdraw(args: argparse.Namespace) -> int:
    _ensure_selftest_passes()
    print("withdraw: not yet implemented")
    return 0


# ----------------------- argparse plumbing -----------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aave",
        description="Aave V3 stable drainer (Python, minimal).",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("selftest", help="run embedded test vectors and report")

    p_verify = sub.add_parser("verify", help="read-only state inspection")
    p_verify.add_argument("--prompt", action="store_true", help="prompt for the mnemonic instead of reading MNEMONIC env var")

    p_withdraw = sub.add_parser("withdraw", help="interactive sequential drainer (DAI -> USDC -> USDT)")
    p_withdraw.add_argument("--only", choices=VALID_MARKETS, help="restrict to a single market")
    p_withdraw.add_argument("--yes", "-y", action="store_true", help="skip per-market confirmation prompts")
    p_withdraw.add_argument("--prompt", action="store_true", help="prompt for the mnemonic instead of reading MNEMONIC env var")

    return p


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "selftest":
        return cmd_selftest(args)
    if args.command == "verify":
        return cmd_verify(args)
    if args.command == "withdraw":
        return cmd_withdraw(args)
    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())

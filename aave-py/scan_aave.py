"""Find which wallet holds the Aave V3 DAI / USDC / USDT supply positions.

Read-only. Derives addresses from every MNEMONIC* env var across a set of HD
paths and reports aToken balances for each. Never signs or broadcasts.

The output identifies the mnemonic env var + HD path pair that `aave.py
verify` / `aave.py withdraw` should be pointed at, without ever printing a
seed phrase.

Usage:
    ./aave scan_aave                          # all MNEMONIC* vars, 5 indices per pattern
    ./aave scan_aave --depth 10               # widen the path search
    ./aave scan_aave --only-var MNEMONIC1     # restrict to one mnemonic
    ./aave scan_aave --prompt                 # type a mnemonic instead of reading .env
    ./aave scan_aave --json manifest.json     # write machine-readable results
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lib import aave as aavelib  # noqa: E402
from lib.aave import MARKET_ORDER, MARKETS  # noqa: E402
from lib.bip39_32 import derive_path, mnemonic_to_seed  # noqa: E402
from lib.crypto import derive_address  # noqa: E402
from lib.jsonrpc import JsonRpcError, RpcClient  # noqa: E402

VALID_WORD_COUNTS = (12, 15, 18, 21, 24)


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
        if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
            v = v[1:-1]
        out[k] = v
    return out


def _load_dotenv(path: str = ".env") -> None:
    for k, v in _parse_env(path).items():
        os.environ.setdefault(k, v)


def _build_paths(depth: int) -> list[str]:
    """MetaMask/Rabby account indices first, then Ledger Live, then legacy MEW."""
    patterns: list[str] = []
    for i in range(depth):
        patterns.append(f"m/44'/60'/0'/0/{i}")
    for i in range(depth):
        patterns.append(f"m/44'/60'/{i}'/0/0")
    for i in range(depth):
        patterns.append(f"m/44'/60'/0'/{i}")
    seen: set[str] = set()
    paths: list[str] = []
    for p in patterns:
        if p not in seen:
            seen.add(p)
            paths.append(p)
    return paths


def _format_units(amount: int, decimals: int) -> str:
    if amount == 0:
        return "0"
    s = str(amount).rjust(decimals + 1, "0")
    whole, frac = s[:-decimals], s[-decimals:]
    frac = frac.rstrip("0")[:6]
    return whole if not frac else f"{whole}.{frac}"


def _collect_mnemonics(args: argparse.Namespace) -> list[tuple[str, str]]:
    if args.prompt:
        m = getpass.getpass("BIP-39 mnemonic: ").strip()
        if not m:
            print("error: empty mnemonic", file=sys.stderr)
            sys.exit(2)
        return [("prompted", m)]

    out: list[tuple[str, str]] = []
    for key, val in sorted(os.environ.items()):
        if not key.startswith("MNEMONIC") or not val.strip():
            continue
        if args.only_var and key != args.only_var:
            continue
        out.append((key, val.strip()))
    return out


def _scan_address(client: RpcClient, addr: str) -> dict[str, int]:
    """Return {market_key: aToken_balance} for every market with a non-zero balance."""
    hits: dict[str, int] = {}
    for key in MARKET_ORDER:
        try:
            bal = aavelib.erc20_balance_of(client, MARKETS[key].a_token, addr)
        except (JsonRpcError, OSError, RuntimeError, ValueError):
            continue
        if bal > 0:
            hits[key] = bal
    return hits


def _describe_hit(client: RpcClient, addr: str, hits: dict[str, int]) -> dict:
    """Deep read for an address that actually holds aTokens."""
    detail: dict = {"address": addr, "markets": {}}
    try:
        detail["eth_wei"] = int(client.call("eth_getBalance", [addr, "latest"]), 16)
    except (JsonRpcError, OSError):
        detail["eth_wei"] = None
    try:
        detail["nonce"] = int(client.call("eth_getTransactionCount", [addr, "latest"]), 16)
    except (JsonRpcError, OSError):
        detail["nonce"] = None
    try:
        acct = aavelib.get_user_account_data(client, addr)
        detail["total_collateral_base"] = acct.total_collateral_base
        detail["total_debt_base"] = acct.total_debt_base
    except (JsonRpcError, OSError, RuntimeError):
        detail["total_collateral_base"] = None
        detail["total_debt_base"] = None

    for key, a_bal in hits.items():
        m = MARKETS[key]
        entry: dict = {"a_token_balance": a_bal, "decimals": m.decimals}
        try:
            entry["pool_liquidity"] = aavelib.erc20_balance_of(client, m.underlying, m.a_token)
        except (JsonRpcError, OSError, RuntimeError):
            entry["pool_liquidity"] = None
        try:
            entry["wallet_balance"] = aavelib.erc20_balance_of(client, m.underlying, addr)
        except (JsonRpcError, OSError, RuntimeError):
            entry["wallet_balance"] = None
        try:
            status = aavelib.decode_reserve_status(
                aavelib.get_reserve_configuration(client, m.underlying)
            )
            entry["reserve_status"] = (
                "INACTIVE" if not status.active
                else "PAUSED" if status.paused
                else "FROZEN" if status.frozen
                else "ok"
            )
        except (JsonRpcError, OSError, RuntimeError):
            entry["reserve_status"] = None
        detail["markets"][key] = entry
    return detail


def _print_hit(label: str, path: str, detail: dict) -> None:
    print()
    print(f"  *** POSITION FOUND ***")
    print(f"      mnemonic var: {label}")
    print(f"      HD path:      {path}")
    print(f"      address:      {detail['address']}")
    if detail.get("eth_wei") is not None:
        print(f"      ETH (gas):    {_format_units(detail['eth_wei'], 18)} ETH")
    if detail.get("nonce") is not None:
        print(f"      nonce:        {detail['nonce']}")
    debt = detail.get("total_debt_base")
    if debt:
        print(f"      open debt:    ${_format_units(debt, 8)}  <-- withdraw's debt guard will refuse this")
    for key, entry in detail["markets"].items():
        dec = entry["decimals"]
        line = f"      {key:<5} supplied {_format_units(entry['a_token_balance'], dec)}"
        if entry.get("wallet_balance"):
            line += f"  (+{_format_units(entry['wallet_balance'], dec)} already in wallet)"
        if entry.get("reserve_status") and entry["reserve_status"] != "ok":
            line += f"  [reserve {entry['reserve_status']}]"
        liq = entry.get("pool_liquidity")
        if liq is not None and liq < entry["a_token_balance"]:
            line += f"  [PARTIAL: pool has only {_format_units(liq, dec)}]"
        print(line)


def main() -> int:
    _load_dotenv()

    parser = argparse.ArgumentParser(
        prog="scan_aave",
        description="Read-only scan for Aave V3 DAI/USDC/USDT supply positions.",
    )
    parser.add_argument("--depth", type=int, default=5,
                        help="indices to try per HD path pattern (default: 5)")
    parser.add_argument("--only-var", metavar="NAME",
                        help="restrict to a single MNEMONIC* env var")
    parser.add_argument("--prompt", action="store_true",
                        help="prompt for one mnemonic instead of reading MNEMONIC* from .env")
    parser.add_argument("--strict-checksum", action="store_true",
                        help="enforce BIP-39 checksum (some wallets emit non-compliant phrases)")
    parser.add_argument("--json", metavar="PATH",
                        help="write results as JSON for the withdraw step to consume")
    args = parser.parse_args()

    url = os.environ.get("ALCHEMY_HTTP_URL", "").strip()
    if not url:
        print("error: ALCHEMY_HTTP_URL not set in .env", file=sys.stderr)
        return 2
    if "eth-mainnet" not in url:
        print(f"note: RPC URL does not look like Ethereum mainnet; Aave addresses are mainnet-only.")
    client = RpcClient(url)

    mnemonics = _collect_mnemonics(args)
    if not mnemonics:
        print("error: no MNEMONIC* vars found in .env and --prompt not set", file=sys.stderr)
        return 2

    paths = _build_paths(args.depth)
    print(f"Scanning {len(mnemonics)} mnemonic(s) x {len(paths)} paths "
          f"= {len(mnemonics) * len(paths)} addresses against Aave V3 mainnet")
    print(f"Markets: {', '.join(MARKET_ORDER)}")
    print()

    found: list[dict] = []
    skipped: list[tuple[str, str]] = []

    for label, mnemonic in mnemonics:
        words = mnemonic.split()
        if len(words) not in VALID_WORD_COUNTS:
            print(f"--- {label}: SKIP ({len(words)} words, need one of {VALID_WORD_COUNTS})")
            skipped.append((label, f"{len(words)} words"))
            continue
        try:
            seed = mnemonic_to_seed(mnemonic, skip_checksum=not args.strict_checksum)
        except ValueError as e:
            print(f"--- {label}: SKIP ({e})")
            skipped.append((label, str(e)))
            continue

        print(f"--- {label} ", end="", flush=True)
        hit_count = 0
        for path in paths:
            try:
                priv = derive_path(seed, path)
            except ValueError:
                continue
            addr = derive_address(priv)
            del priv
            hits = _scan_address(client, addr)
            if not hits:
                sys.stdout.write(".")
                sys.stdout.flush()
                continue
            detail = _describe_hit(client, addr, hits)
            detail["mnemonic_var"] = label
            detail["hd_path"] = path
            _print_hit(label, path, detail)
            found.append(detail)
            hit_count += 1
        del seed
        if hit_count == 0:
            print("  no positions")
        print()

    print("=" * 72)
    if not found:
        print("  NO AAVE POSITIONS FOUND")
        print("=" * 72)
        print(f"  Checked {len(mnemonics)} mnemonic(s) x {len(paths)} paths.")
        if skipped:
            print(f"  {len(skipped)} mnemonic(s) skipped: "
                  + ", ".join(f"{l} ({r})" for l, r in skipped))
        print("  Try a wider --depth, or --prompt with the seed you expect to hold the position.")
    else:
        print(f"  {len(found)} WALLET(S) WITH AAVE POSITIONS")
        print("=" * 72)
        for detail in found:
            print()
            print(f"  {detail['address']}")
            print(f"    run withdraw with:  MNEMONIC_VAR={detail['mnemonic_var']} "
                  f"HD_PATH={detail['hd_path']}")
            total_markets = ", ".join(
                f"{k} {_format_units(v['a_token_balance'], v['decimals'])}"
                for k, v in detail["markets"].items()
            )
            print(f"    supplied:           {total_markets}")
            if detail.get("eth_wei") is not None and detail["eth_wei"] < 5 * 10 ** 15:
                print(f"    !! low gas:         {_format_units(detail['eth_wei'], 18)} ETH "
                      f"(want >=0.005 for a 3-market drain)")

    if args.json:
        Path(args.json).write_text(json.dumps(found, indent=2), encoding="utf-8")
        print()
        print(f"  wrote {args.json}")

    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())

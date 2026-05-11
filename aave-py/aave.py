"""Aave V3 Stable Drainer (Python, minimal) — CLI entrypoint."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Make sibling `lib/` importable when running `python aave.py ...` directly.
sys.path.insert(0, str(Path(__file__).parent))

from lib import vectors  # noqa: E402

import getpass  # noqa: E402
from lib import aave as aavelib  # noqa: E402
from lib.aave import MARKET_ORDER, MARKETS, POOL, decode_reserve_status  # noqa: E402
from lib.bip39_32 import derive_path, mnemonic_to_seed  # noqa: E402
from lib.crypto import derive_address  # noqa: E402
from lib.eth import build_eip1559_tx, sign_eip1559_tx  # noqa: E402
from lib.jsonrpc import JsonRpcError, RpcClient  # noqa: E402


VERSION = "0.1.0"
VALID_MARKETS = ("DAI", "USDC", "USDT")
DEFAULT_HD_PATH = "m/44'/60'/0'/0/0"


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


# ----------------------- shared helpers -----------------------

def _make_client() -> RpcClient:
    url = os.environ.get("ALCHEMY_HTTP_URL")
    if not url:
        print("Missing ALCHEMY_HTTP_URL. Set it in .env or the environment.")
        sys.exit(2)
    return RpcClient(url)


def _resolve_mnemonic(prompt: bool) -> str:
    if prompt:
        m = getpass.getpass("BIP-39 mnemonic: ").strip()
        if not m:
            print("Empty mnemonic. Aborting.")
            sys.exit(2)
        return m
    m = os.environ.get("MNEMONIC", "").strip()
    if not m:
        print("MNEMONIC not set in .env. Pass --prompt to enter it interactively.")
        sys.exit(2)
    return m


def _resolve_wallet(prompt: bool) -> tuple[bytes, str]:
    mnemonic = _resolve_mnemonic(prompt)
    path = os.environ.get("HD_PATH", DEFAULT_HD_PATH).strip()
    seed = mnemonic_to_seed(mnemonic)
    priv = derive_path(seed, path)
    addr = derive_address(priv)
    return priv, addr


def _format_units(amount: int, decimals: int) -> str:
    if amount == 0:
        return "0"
    s = str(amount).rjust(decimals + 1, "0")
    whole, frac = s[:-decimals], s[-decimals:]
    frac = frac.rstrip("0")
    return whole if not frac else f"{whole}.{frac}"


def _divider(label: str) -> None:
    print()
    print("=" * 72)
    print(label)
    print("=" * 72)


# ----------------------- verify -----------------------

def cmd_verify(args: argparse.Namespace) -> int:
    _ensure_selftest_passes()
    client = _make_client()
    priv, addr = _resolve_wallet(prompt=getattr(args, "prompt", False))
    recipient = os.environ.get("RECIPIENT", "").strip() or addr

    _divider("RPC + wallet")
    block_hex = client.call("eth_blockNumber", [])
    eth_balance_hex = client.call("eth_getBalance", [addr, "latest"])
    nonce_hex = client.call("eth_getTransactionCount", [addr, "latest"])
    print(f"Block:           {int(block_hex, 16)}")
    print(f"Pool:            {POOL}")
    print(f"HD path:         {os.environ.get('HD_PATH', DEFAULT_HD_PATH)}")
    print(f"Wallet:          {addr}")
    same = "  (same as wallet)" if recipient == addr else "  (override via RECIPIENT)"
    print(f"Recipient:       {recipient}{same}")
    print(f"ETH balance:     {_format_units(int(eth_balance_hex, 16), 18)} ETH")
    print(f"Nonce (latest):  {int(nonce_hex, 16)}")

    # Wipe local copy of priv ASAP - verify never signs anything.
    del priv

    _divider("Aave account state")
    acct = aavelib.get_user_account_data(client, addr)
    print(f"totalCollateralBase:  ${_format_units(acct.total_collateral_base, 8)}")
    print(f"totalDebtBase:        ${_format_units(acct.total_debt_base, 8)}")
    print(f"availableBorrowsBase: ${_format_units(acct.available_borrows_base, 8)}")
    print(f"liquidationThreshold: {_format_units(acct.current_liquidation_threshold, 2)}%")
    print(f"ltv:                  {_format_units(acct.ltv, 2)}%")
    if acct.health_factor == (1 << 256) - 1:
        print("healthFactor:         INFINITE (no debt)")
    else:
        print(f"healthFactor:         {_format_units(acct.health_factor, 18)}")
    if acct.total_debt_base > 0:
        print()
        print("!!! WARNING: this account has open Aave debt.")
        print("    Withdraw can revert with HEALTH_FACTOR_LOWER_THAN_LIQUIDATION_THRESHOLD.")

    _divider("Per-market state")
    summary_keys = []
    for key in MARKET_ORDER:
        m = MARKETS[key]
        balance = aavelib.erc20_balance_of(client, m.a_token, addr)
        liquidity = aavelib.erc20_balance_of(client, m.underlying, m.a_token)
        symbol = aavelib.erc20_symbol(client, m.underlying)
        config = aavelib.get_reserve_configuration(client, m.underlying)
        status = decode_reserve_status(config)
        status_str = (
            "INACTIVE" if not status.active
            else "PAUSED" if status.paused
            else "FROZEN" if status.frozen
            else "ok"
        )
        print()
        print(f"-- {key} ({symbol})")
        print(f"   underlying:       {m.underlying}")
        print(f"   aToken:           {m.a_token}")
        print(f"   reserve status:   {status_str}")
        print(f"   your aToken bal:  {_format_units(balance, m.decimals)} {symbol}  (raw {balance})")
        print(f"   pool liquidity:   {_format_units(liquidity, m.decimals)} {symbol}")
        if balance > 0 and balance > liquidity:
            print(f"   !!! pool liquidity ({_format_units(liquidity, m.decimals)}) is LESS than your balance.")
            print(f"       withdraw will fall back to a partial draw.")
        if balance > 0 and status.active and not status.paused:
            summary_keys.append(key)

    _divider("Summary")
    print(f"Wallet:                {addr}")
    if not summary_keys:
        print("Markets with non-zero withdrawable balance: NONE.")
    else:
        print(f"Markets with non-zero withdrawable balance: {', '.join(summary_keys)}")
    print()
    print("If everything looks right, run `python aave.py withdraw`.")
    return 0


# ----------------------- withdraw -----------------------

def _gas_fields(client: RpcClient) -> tuple[int, int]:
    priority_gwei = int(os.environ.get("PRIORITY_FEE_GWEI", "2"))
    priority = priority_gwei * 10 ** 9
    max_fee_gwei = os.environ.get("MAX_FEE_GWEI", "").strip()
    if max_fee_gwei:
        return int(max_fee_gwei) * 10 ** 9, priority
    block = client.call("eth_getBlockByNumber", ["latest", False])
    base_fee = int(block["baseFeePerGas"], 16)
    return base_fee * 2 + priority, priority


def _confirm(prompt: str, auto: bool) -> bool:
    if auto:
        return True
    try:
        a = input(prompt + " [y/N]: ").strip().lower()
    except EOFError:
        return False
    return a in ("y", "yes")


def _try_estimate_gas(client: RpcClient, sender: str, data: bytes) -> int:
    try:
        out = client.call(
            "eth_estimateGas",
            [{"from": sender, "to": POOL, "data": "0x" + data.hex(), "value": "0x0"}],
        )
        return int(out, 16)
    except JsonRpcError as e:
        raise RuntimeError(f"eth_estimateGas reverted: {e}") from e


def _wait_for_receipt(client: RpcClient, tx_hash: str, timeout_secs: int = 300, poll_secs: float = 4.0):
    import time
    deadline = time.monotonic() + timeout_secs
    while time.monotonic() < deadline:
        receipt = client.call("eth_getTransactionReceipt", [tx_hash])
        if receipt is not None:
            return receipt
        time.sleep(poll_secs)
    return None


def cmd_withdraw(args: argparse.Namespace) -> int:
    _ensure_selftest_passes()
    client = _make_client()
    priv, addr = _resolve_wallet(prompt=getattr(args, "prompt", False))
    recipient = os.environ.get("RECIPIENT", "").strip() or addr
    only = getattr(args, "only", None)
    auto_yes = getattr(args, "yes", False) or os.environ.get("AUTO_CONFIRM", "").lower() == "true"
    gas_limit = int(os.environ.get("GAS_LIMIT", "500000"))

    _divider("Pre-flight")
    eth_balance_hex = client.call("eth_getBalance", [addr, "latest"])
    eth_wei = int(eth_balance_hex, 16)
    print(f"Wallet:     {addr}")
    print(f"Recipient:  {recipient}{'  (override)' if recipient != addr else ''}")
    print(f"ETH:        {_format_units(eth_wei, 18)} ETH")
    print(f"AUTO yes:   {auto_yes}")
    if only:
        print(f"--only:     {only}")

    acct = aavelib.get_user_account_data(client, addr)
    if acct.total_debt_base > 0:
        print()
        print("!!! Wallet has open Aave debt. Refusing to start withdrawal sequence.")
        print(f"    totalDebtBase = ${_format_units(acct.total_debt_base, 8)}")
        sys.exit(2)

    if eth_wei < 5 * 10 ** 15:  # 0.005 ETH
        print()
        print("!!! WARNING: ETH balance is very low (<0.005 ETH). Top up before continuing.")

    targets = (only,) if only else MARKET_ORDER
    results: list[tuple[str, str]] = []

    for key in targets:
        m = MARKETS[key]
        _divider(f"Market: {key}")

        balance = aavelib.erc20_balance_of(client, m.a_token, addr)
        if balance == 0:
            print(f"[{key}] aToken balance is 0 - nothing to withdraw, skipping.")
            results.append((key, "skipped"))
            continue

        config = aavelib.get_reserve_configuration(client, m.underlying)
        status = decode_reserve_status(config)
        if not status.active:
            print(f"[{key}] reserve INACTIVE - skipping.")
            results.append((key, "skipped"))
            continue
        if status.paused:
            print(f"[{key}] reserve PAUSED - skipping.")
            results.append((key, "skipped"))
            continue

        liquidity = aavelib.erc20_balance_of(client, m.underlying, m.a_token)
        if liquidity == 0:
            print(f"[{key}] pool has 0 liquidity right now - skipping.")
            results.append((key, "skipped"))
            continue
        if liquidity >= balance:
            amount = (1 << 256) - 1
            mode = "ALL"
        else:
            amount = liquidity - 1 if liquidity > 1 else liquidity
            mode = "PARTIAL"
            print(f"[{key}] partial withdraw: pool liquidity {liquidity} < balance {balance}")

        data = aavelib.withdraw_calldata(asset=m.underlying, amount=amount, to=recipient)
        try:
            est = _try_estimate_gas(client, addr, data)
        except RuntimeError as e:
            print(str(e))
            if not _confirm("Continue with the next market?", auto_yes):
                sys.exit(1)
            results.append((key, "aborted"))
            continue

        if est * 12 // 10 > gas_limit:
            print(f"[{key}] gas estimate {est} too close to GAS_LIMIT {gas_limit}; bump it.")
            sys.exit(1)

        max_fee, priority = _gas_fields(client)
        balance_fmt = _format_units(balance, m.decimals)
        amount_fmt = balance_fmt if mode == "ALL" else _format_units(amount, m.decimals)

        print()
        print(f"  asset:           {m.underlying}")
        print(f"  current balance: {balance_fmt}")
        print(f"  withdrawing:     {mode} ({amount_fmt})")
        print(f"  recipient:       {recipient}")
        print(f"  gas estimate:    {est}")
        print(f"  gas limit:       {gas_limit}")
        print(f"  maxFeePerGas:    {max_fee // 10 ** 9} gwei")
        print(f"  priorityFee:     {priority // 10 ** 9} gwei")
        print(f"  est tx cost:     ~{_format_units(max_fee * est, 18)} ETH")

        if not _confirm("  Proceed with this withdrawal?", auto_yes):
            print("  Skipped by user.")
            results.append((key, "aborted"))
            continue

        nonce = int(client.call("eth_getTransactionCount", [addr, "latest"]), 16)
        tx = build_eip1559_tx(
            chain_id=1, nonce=nonce,
            max_priority_fee_per_gas=priority, max_fee_per_gas=max_fee,
            gas_limit=gas_limit, to=POOL, value=0, data=data,
        )
        raw = sign_eip1559_tx(tx, priv)
        try:
            tx_hash = client.call("eth_sendRawTransaction", ["0x" + raw.hex()])
        except JsonRpcError as e:
            print(f"  !!! send rejected: {e}")
            sys.exit(1)
        print(f"  tx hash:         {tx_hash}")
        print(f"  https://etherscan.io/tx/{tx_hash}")
        print("  waiting for receipt...")
        receipt = _wait_for_receipt(client, tx_hash)
        if receipt is None:
            print("  receipt timeout; check etherscan.")
            sys.exit(4)
        block = int(receipt["blockNumber"], 16)
        status_int = int(receipt["status"], 16)
        gas_used = int(receipt["gasUsed"], 16)
        print(f"  mined in block {block}, status={status_int}, gasUsed={gas_used}")
        if status_int != 1:
            print("  !!! tx REVERTED. Stopping.")
            sys.exit(1)
        results.append((key, "withdrawn"))

    # Wipe priv after the last sign.
    del priv

    _divider("Done")
    for k, st in results:
        print(f"  {k.ljust(5)} {st}")
    print()
    print("Recommended: run `python aave.py verify` again to confirm balances are zero.")
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

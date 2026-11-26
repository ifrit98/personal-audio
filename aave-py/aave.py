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
from lib import curve as curvelib  # noqa: E402
from lib.aave import MARKET_ORDER, MARKETS, POOL, decode_reserve_status  # noqa: E402
from lib.eth import build_eip1559_tx  # noqa: E402
from lib.jsonrpc import JsonRpcError, RpcClient  # noqa: E402
from lib.signer import SignerError, assert_expected_address, build_signer  # noqa: E402


VERSION = "0.2.0"
VALID_MARKETS = ("DAI", "USDC", "USDT")
DEFAULT_HD_PATH = "m/44'/60'/0'/0/0"

# totalDebtBase is USD scaled by 1e8. Aave leaves sub-cent rounding dust on
# accounts that have ever borrowed, and refusing to withdraw over $0.000001
# would block every real drain, so only debt above this floor is treated as real.
DEFAULT_DEBT_DUST_BASE = 10 ** 6  # $0.01


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


def _int_env(name: str, default: str | None = None) -> int:
    """Read an integer env var, exiting cleanly with a clear message on parse failure."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        if default is None:
            print(f"Missing required env var: {name}")
            sys.exit(2)
        raw = default
    try:
        return int(raw)
    except ValueError:
        print(f"Env var {name} must be an integer; got {raw!r}.")
        sys.exit(2)


def _available_mnemonic_vars() -> list[str]:
    return sorted(k for k, v in os.environ.items() if k.startswith("MNEMONIC") and v.strip())


def _resolve_mnemonic(args: argparse.Namespace) -> str:
    if getattr(args, "prompt", False):
        m = getpass.getpass("BIP-39 mnemonic: ").strip()
        if not m:
            print("Empty mnemonic. Aborting.")
            sys.exit(2)
        return m

    var = getattr(args, "mnemonic_var", None) or os.environ.get("MNEMONIC_VAR", "").strip() or "MNEMONIC"
    m = os.environ.get(var, "").strip()
    if m:
        return m

    print(f"{var} is not set in .env or the environment.")
    others = [v for v in _available_mnemonic_vars() if v != var]
    if others:
        print(f"  Seed phrases found under other names: {', '.join(others)}")
        print(f"  Point at one with --mnemonic-var NAME (no need to copy the seed).")
    print("  Or pass --prompt to type it interactively.")
    sys.exit(2)


def _resolve_passphrase(args: argparse.Namespace) -> str:
    """BIP-39 passphrase (the '25th word'). Changes every derived address."""
    if getattr(args, "passphrase", False):
        return getpass.getpass("BIP-39 passphrase (blank for none): ")
    return os.environ.get("BIP39_PASSPHRASE", "")


def _resolve_signer(args: argparse.Namespace):
    """Build the signing backend and verify it resolves to the expected address."""
    kind = (
        getattr(args, "signer", None)
        or os.environ.get("SIGNER", "").strip().lower()
        or "mnemonic"
    )

    if kind == "ledger":
        hd_path = (
            getattr(args, "hd_path", None)
            or os.environ.get("LEDGER_PATH", "").strip()
            or os.environ.get("HD_PATH", "").strip()
            or DEFAULT_HD_PATH
        )
        mnemonic = None
        passphrase = ""
    else:
        hd_path = (
            getattr(args, "hd_path", None)
            or os.environ.get("HD_PATH", "").strip()
            or DEFAULT_HD_PATH
        )
        mnemonic = _resolve_mnemonic(args)
        passphrase = _resolve_passphrase(args)

    try:
        signer = build_signer(
            kind=kind,
            hd_path=hd_path,
            mnemonic=mnemonic,
            passphrase=passphrase,
            skip_checksum=getattr(args, "skip_checksum", False),
        )
    except SignerError as e:
        print(f"Signer error: {e}")
        sys.exit(2)

    expected = (
        getattr(args, "expect_address", None)
        or os.environ.get("EXPECTED_ADDRESS", "").strip()
        or None
    )
    try:
        assert_expected_address(signer, expected)
    except SignerError as e:
        print(f"!!! {e}")
        signer.close()
        sys.exit(2)

    return signer


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
    signer = _resolve_signer(args)
    addr = signer.address
    recipient = os.environ.get("RECIPIENT", "").strip() or addr

    _divider("RPC + wallet")
    block_hex = client.call("eth_blockNumber", [])
    eth_balance_hex = client.call("eth_getBalance", [addr, "latest"])
    nonce_hex = client.call("eth_getTransactionCount", [addr, "latest"])
    print(f"Block:           {int(block_hex, 16)}")
    print(f"Pool:            {POOL}")
    print(f"Signer:          {signer.describe()}")
    print(f"Wallet:          {addr}")
    same = "  (same as wallet)" if recipient == addr else "  (override via RECIPIENT)"
    print(f"Recipient:       {recipient}{same}")
    print(f"ETH balance:     {_format_units(int(eth_balance_hex, 16), 18)} ETH")
    print(f"Nonce (latest):  {int(nonce_hex, 16)}")

    # verify never signs anything; release the key immediately.
    signer.close()

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
    priority_gwei = _int_env("PRIORITY_FEE_GWEI", "2")
    priority = priority_gwei * 10 ** 9
    max_fee_gwei = os.environ.get("MAX_FEE_GWEI", "").strip()
    if max_fee_gwei:
        try:
            return int(max_fee_gwei) * 10 ** 9, priority
        except ValueError:
            print(f"Env var MAX_FEE_GWEI must be an integer; got {max_fee_gwei!r}.")
            sys.exit(2)
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
    """Poll eth_getTransactionReceipt until it returns a non-null receipt or
    the timeout expires.

    Once a tx has been broadcast, transient RPC errors (rate limits, node
    blips, network errors) MUST NOT be treated as the tx having failed -
    the tx may already be in the mempool or even mined. We swallow those
    here and keep polling, with a one-line warning so the user knows
    polling is degraded.
    """
    import time
    from urllib.error import URLError
    deadline = time.monotonic() + timeout_secs
    while time.monotonic() < deadline:
        try:
            receipt = client.call("eth_getTransactionReceipt", [tx_hash])
        except (JsonRpcError, URLError, TimeoutError) as e:
            print(f"  (transient RPC error while polling receipt: {e}; will retry)")
            time.sleep(poll_secs)
            continue
        if receipt is not None:
            return receipt
        time.sleep(poll_secs)
    return None


def cmd_withdraw(args: argparse.Namespace) -> int:
    _ensure_selftest_passes()
    client = _make_client()
    signer = _resolve_signer(args)
    addr = signer.address
    recipient = os.environ.get("RECIPIENT", "").strip() or addr
    only = getattr(args, "only", None)
    auto_yes = getattr(args, "yes", False) or os.environ.get("AUTO_CONFIRM", "").lower() == "true"
    gas_limit = _int_env("GAS_LIMIT", "500000")

    _divider("Pre-flight")
    eth_balance_hex = client.call("eth_getBalance", [addr, "latest"])
    eth_wei = int(eth_balance_hex, 16)
    print(f"Signer:     {signer.describe()}")
    print(f"Wallet:     {addr}")
    print(f"Recipient:  {recipient}{'  (override)' if recipient != addr else ''}")
    print(f"ETH:        {_format_units(eth_wei, 18)} ETH")
    print(f"AUTO yes:   {auto_yes}")
    if only:
        print(f"--only:     {only}")

    acct = aavelib.get_user_account_data(client, addr)
    debt_floor = _int_env("DEBT_DUST_THRESHOLD_BASE", str(DEFAULT_DEBT_DUST_BASE))
    if acct.total_debt_base > debt_floor and not getattr(args, "allow_debt", False):
        print()
        print("!!! Wallet has open Aave debt. Refusing to start withdrawal sequence.")
        print(f"    totalDebtBase = ${_format_units(acct.total_debt_base, 8)}")
        print(f"    dust floor    = ${_format_units(debt_floor, 8)}")
        print("    Repay the borrow first, or pass --allow-debt if you accept the")
        print("    HEALTH_FACTOR_LOWER_THAN_LIQUIDATION_THRESHOLD revert risk.")
        signer.close()
        sys.exit(2)
    if 0 < acct.total_debt_base <= debt_floor:
        print(f"    (ignoring ${_format_units(acct.total_debt_base, 8)} of rounding dust debt)")

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
        if signer.kind == "ledger":
            print("  confirm on the Ledger...")
        try:
            raw = signer.sign(tx)
        except SignerError as e:
            print(f"  !!! signing failed: {e}")
            signer.close()
            sys.exit(1)
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

    signer.close()

    _divider("Done")
    for k, st in results:
        print(f"  {k.ljust(5)} {st}")
    print()
    print("Recommended: run `python aave.py verify` again to confirm balances are zero.")
    return 0


# ----------------------- swap -----------------------

def _send_and_wait(client, signer, label, tx, gas_limit_hint: str) -> bool:
    """Sign, broadcast, and await a receipt. Returns True on a successful mine."""
    if signer.kind == "ledger":
        print(f"  confirm the {label} on the Ledger...")
    try:
        raw = signer.sign(tx)
    except SignerError as e:
        print(f"  !!! signing failed: {e}")
        return False
    try:
        tx_hash = client.call("eth_sendRawTransaction", ["0x" + raw.hex()])
    except JsonRpcError as e:
        print(f"  !!! send rejected: {e}")
        return False
    print(f"  tx hash:  {tx_hash}")
    print(f"  https://etherscan.io/tx/{tx_hash}")
    print("  waiting for receipt...")
    receipt = _wait_for_receipt(client, tx_hash)
    if receipt is None:
        print("  receipt timeout; check etherscan before retrying "
              "(a resend would double-spend the nonce).")
        return False
    status_int = int(receipt["status"], 16)
    print(f"  mined in block {int(receipt['blockNumber'], 16)}, "
          f"status={status_int}, gasUsed={int(receipt['gasUsed'], 16)}")
    if status_int != 1:
        print(f"  !!! {label} REVERTED. {gas_limit_hint}")
        return False
    return True


def cmd_swap(args: argparse.Namespace) -> int:
    """Consolidate stablecoin balances into one asset via Curve 3pool."""
    _ensure_selftest_passes()
    client = _make_client()
    signer = _resolve_signer(args)
    addr = signer.address
    auto_yes = getattr(args, "yes", False) or os.environ.get("AUTO_CONFIRM", "").lower() == "true"
    slippage_bps = getattr(args, "slippage_bps", None) or _int_env("SLIPPAGE_BPS", "10")

    target_key = args.to
    target = curvelib.COINS[target_key]
    sources = [args.source] if args.source else [k for k in ("DAI", "USDC", "USDT") if k != target_key]
    if target_key in sources:
        print(f"Cannot swap {target_key} into itself.")
        signer.close()
        return 2

    _divider("Pre-flight")
    print(f"Signer:     {signer.describe()}")
    print(f"Wallet:     {addr}")
    print(f"Pool:       {curvelib.POOL}  (Curve 3pool)")
    print(f"Target:     {target_key}")
    print(f"Slippage:   {slippage_bps} bps")

    # Never trade against an unverified coin layout.
    try:
        curvelib.assert_coin_layout(client)
        print("Coin layout: verified against the live pool (DAI=0, USDC=1, USDT=2)")
    except (RuntimeError, JsonRpcError) as e:
        print(f"!!! {e}")
        signer.close()
        return 1

    fee = curvelib.pool_fee(client)
    print(f"Pool fee:   {fee} / 1e10  ({fee / curvelib.FEE_DENOMINATOR:.4%})")

    eth_wei = int(client.call("eth_getBalance", [addr, "latest"]), 16)
    print(f"ETH:        {_format_units(eth_wei, 18)} ETH")
    if eth_wei < 3 * 10 ** 15:
        print("!!! WARNING: under 0.003 ETH. An approve + exchange pair may not fit.")

    gas_limit = _int_env("SWAP_GAS_LIMIT", "400000")
    legs: list[tuple[curvelib.Coin, int, int, int]] = []

    for key in sources:
        coin = curvelib.COINS[key]
        balance = aavelib.erc20_balance_of(client, coin.address, addr)
        if balance == 0:
            print(f"\n[{key}] wallet balance is 0 - nothing to swap.")
            continue
        quoted = curvelib.get_dy(client, coin.index, target.index, balance)
        min_dy = curvelib.min_dy_with_slippage(quoted, slippage_bps)
        legs.append((coin, balance, quoted, min_dy))

        in_fmt = _format_units(balance, coin.decimals)
        out_fmt = _format_units(quoted, target.decimals)
        cost = balance * 10 ** target.decimals // 10 ** coin.decimals - quoted
        print(f"\n[{key}] -> {target_key}")
        print(f"  in:       {in_fmt} {key}")
        print(f"  quoted:   {out_fmt} {target_key}  (on-chain get_dy)")
        print(f"  min out:  {_format_units(min_dy, target.decimals)} {target_key}")
        print(f"  cost:     ~{_format_units(max(cost, 0), target.decimals)} {target_key} "
              f"vs a 1:1 swap")

    if not legs:
        print("\nNothing to swap.")
        signer.close()
        return 0

    _divider("Execution plan")
    for coin, balance, quoted, min_dy in legs:
        current = curvelib.allowance(client, coin.address, addr, curvelib.POOL)
        need_approve = current < balance
        print(f"  {coin.key}: "
              f"{'approve + exchange (2 txs)' if need_approve else 'exchange only (allowance sufficient)'}")

    if not _confirm("\nProceed?", auto_yes):
        print("Aborted by user.")
        signer.close()
        return 1

    max_fee, priority = _gas_fields(client)

    for coin, balance, quoted, min_dy in legs:
        _divider(f"Swap: {coin.key} -> {target_key}")

        # Re-quote immediately before executing: the earlier number may be stale.
        fresh = curvelib.get_dy(client, coin.index, target.index, balance)
        min_dy = curvelib.min_dy_with_slippage(fresh, slippage_bps)
        print(f"  re-quoted: {_format_units(fresh, target.decimals)} {target_key} "
              f"(min {_format_units(min_dy, target.decimals)})")

        current = curvelib.allowance(client, coin.address, addr, curvelib.POOL)
        if current < balance:
            print(f"\n  approve {coin.key} -> pool for exactly {_format_units(balance, coin.decimals)}")
            nonce = int(client.call("eth_getTransactionCount", [addr, "latest"]), 16)
            data = curvelib.approve_calldata(curvelib.POOL, balance)
            tx = build_eip1559_tx(
                chain_id=1, nonce=nonce,
                max_priority_fee_per_gas=priority, max_fee_per_gas=max_fee,
                gas_limit=100_000, to=coin.address, value=0, data=data,
            )
            if not _send_and_wait(client, signer, "approve", tx, "Raise the gas limit and retry."):
                signer.close()
                return 1

        print(f"\n  exchange {coin.key} -> {target_key}")
        try:
            est = int(client.call("eth_estimateGas", [{
                "from": addr, "to": curvelib.POOL,
                "data": "0x" + curvelib.exchange_calldata(
                    coin.index, target.index, balance, min_dy).hex(),
                "value": "0x0",
            }]), 16)
        except JsonRpcError as e:
            print(f"  !!! eth_estimateGas reverted: {e}")
            print("      Most likely the approve has not settled, or min_dy is too tight.")
            signer.close()
            return 1
        if est * 12 // 10 > gas_limit:
            print(f"  gas estimate {est} too close to SWAP_GAS_LIMIT {gas_limit}; bump it.")
            signer.close()
            return 1
        print(f"  gas estimate: {est} (limit {gas_limit})")

        nonce = int(client.call("eth_getTransactionCount", [addr, "latest"]), 16)
        data = curvelib.exchange_calldata(coin.index, target.index, balance, min_dy)
        tx = build_eip1559_tx(
            chain_id=1, nonce=nonce,
            max_priority_fee_per_gas=priority, max_fee_per_gas=max_fee,
            gas_limit=gas_limit, to=curvelib.POOL, value=0, data=data,
        )
        if not _send_and_wait(client, signer, "exchange", tx,
                              "min_dy may have been breached; re-run to re-quote."):
            signer.close()
            return 1

    signer.close()

    _divider("Done")
    final = aavelib.erc20_balance_of(client, target.address, addr)
    print(f"  {target_key} balance now: {_format_units(final, target.decimals)}")
    return 0


# ----------------------- argparse plumbing -----------------------

def _add_signer_args(p: argparse.ArgumentParser) -> None:
    """Options shared by every subcommand that needs to resolve a wallet."""
    g = p.add_argument_group("wallet / signing")
    g.add_argument("--signer", choices=("mnemonic", "ledger"),
                   help="signing backend (default: mnemonic, or the SIGNER env var)")
    g.add_argument("--prompt", action="store_true",
                   help="prompt for the mnemonic instead of reading it from the environment")
    g.add_argument("--mnemonic-var", metavar="NAME",
                   help="read the seed from this env var instead of MNEMONIC "
                        "(avoids copying a seed to a second key)")
    g.add_argument("--passphrase", action="store_true",
                   help="prompt for a BIP-39 passphrase (the '25th word'); "
                        "changes every derived address")
    g.add_argument("--hd-path", metavar="PATH",
                   help=f"override the derivation path (default: {DEFAULT_HD_PATH})")
    g.add_argument("--expect-address", metavar="ADDR",
                   help="abort unless the signer resolves to this address")
    g.add_argument("--skip-checksum", action="store_true",
                   help="accept a mnemonic that fails its BIP-39 checksum")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aave",
        description="Aave V3 stable drainer + Curve consolidation (Python, minimal).",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("selftest", help="run embedded test vectors and report")

    p_verify = sub.add_parser("verify", help="read-only state inspection")
    _add_signer_args(p_verify)

    p_withdraw = sub.add_parser("withdraw", help="interactive sequential drainer (DAI -> USDC -> USDT)")
    p_withdraw.add_argument("--only", choices=VALID_MARKETS, help="restrict to a single market")
    p_withdraw.add_argument("--yes", "-y", action="store_true", help="skip per-market confirmation prompts")
    p_withdraw.add_argument("--allow-debt", action="store_true",
                            help="withdraw even with real open debt (risks a health-factor revert)")
    _add_signer_args(p_withdraw)

    p_swap = sub.add_parser("swap", help="consolidate stables into one asset via Curve 3pool")
    p_swap.add_argument("--to", choices=VALID_MARKETS, default="DAI",
                        help="asset to consolidate into (default: DAI)")
    p_swap.add_argument("--source", choices=VALID_MARKETS,
                        help="swap only this asset (default: every non-target asset with a balance)")
    p_swap.add_argument("--slippage-bps", type=int,
                        help="max acceptable slippage vs the on-chain quote, in bps (default: 10)")
    p_swap.add_argument("--yes", "-y", action="store_true", help="skip the confirmation prompt")
    _add_signer_args(p_swap)

    return p


def main(argv: list[str] | None = None, env_path: str | None = ".env") -> int:
    """CLI entry point.

    `env_path` is the path to a dotenv file to load (default: ".env" in cwd).
    Pass `env_path=None` to skip dotenv loading entirely - useful for tests
    so a stray `aave-py/.env` doesn't pollute `os.environ` for the rest of
    the process.
    """
    if env_path is not None:
        _load_dotenv(env_path)
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "selftest":
        return cmd_selftest(args)
    if args.command == "verify":
        return cmd_verify(args)
    if args.command == "withdraw":
        return cmd_withdraw(args)
    if args.command == "swap":
        return cmd_swap(args)
    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())

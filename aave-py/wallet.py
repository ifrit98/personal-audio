"""Wallet inspector — check balances and recent transfer history from a seed phrase.

Supports multiple chains (Ethereum, Base, etc.) and custom token lookups.

Usage:
    ./aave wallet                              # ETH mainnet, reads MNEMONIC from .env
    ./aave wallet --chain base                 # Base mainnet
    ./aave wallet --chain base --token FLOPS:0x66641e5919D4296F7c396fA3205428F9cE8d1B1D:18
    ./aave wallet --prompt --last 25           # interactive input, 25 transfers
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lib.bip39_32 import derive_path, mnemonic_to_seed  # noqa: E402
from lib.crypto import derive_address  # noqa: E402
from lib.jsonrpc import RpcClient  # noqa: E402
from lib.abi import encode_address  # noqa: E402

DEFAULT_HD_PATH = "m/44'/60'/0'/0/0"

CHAINS: dict[str, dict] = {
    "eth": {
        "name": "Ethereum Mainnet",
        "alchemy_subdomain": "eth-mainnet",
        "explorer": "https://etherscan.io",
        "tokens": {
            "ETH":  {"address": None, "decimals": 18},
            "WETH": {"address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "decimals": 18},
            "DAI":  {"address": "0x6B175474E89094C44Da98b954EedeAC495271d0F", "decimals": 18},
            "USDC": {"address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "decimals": 6},
            "USDT": {"address": "0xdAC17F958D2ee523a2206206994597C13D831ec7", "decimals": 6},
            "WBTC": {"address": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", "decimals": 8},
        },
    },
    "base": {
        "name": "Base Mainnet",
        "alchemy_subdomain": "base-mainnet",
        "explorer": "https://basescan.org",
        "tokens": {
            "ETH":  {"address": None, "decimals": 18},
            "WETH": {"address": "0x4200000000000000000000000000000000000006", "decimals": 18},
            "USDC": {"address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "decimals": 6},
            "USDbC": {"address": "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA", "decimals": 6},
            "DAI":  {"address": "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb", "decimals": 18},
        },
    },
    "arb": {
        "name": "Arbitrum One",
        "alchemy_subdomain": "arb-mainnet",
        "explorer": "https://arbiscan.io",
        "tokens": {
            "ETH":  {"address": None, "decimals": 18},
            "WETH": {"address": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", "decimals": 18},
            "USDC": {"address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", "decimals": 6},
            "USDT": {"address": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", "decimals": 6},
            "ARB":  {"address": "0x912CE59144191C1204E64559FE8253a0e49E6548", "decimals": 18},
        },
    },
    "opt": {
        "name": "Optimism",
        "alchemy_subdomain": "opt-mainnet",
        "explorer": "https://optimistic.etherscan.io",
        "tokens": {
            "ETH":  {"address": None, "decimals": 18},
            "WETH": {"address": "0x4200000000000000000000000000000000000006", "decimals": 18},
            "USDC": {"address": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85", "decimals": 6},
            "USDT": {"address": "0x94b008aA00579c1307B0EF2c499aD98a8ce58e58", "decimals": 6},
            "OP":   {"address": "0x4200000000000000000000000000000000000042", "decimals": 18},
        },
    },
}

BALANCE_OF_SELECTOR = "70a08231"
DECIMALS_SELECTOR = "313ce567"
SYMBOL_SELECTOR = "95d89b41"


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


def _resolve_mnemonic(args: argparse.Namespace) -> str:
    if getattr(args, "prompt", False):
        return getpass.getpass("Mnemonic: ")
    m = os.environ.get("MNEMONIC", "").strip()
    if not m:
        print("error: no MNEMONIC in env and --prompt not set", file=sys.stderr)
        sys.exit(2)
    return m


def _format_units(raw: int, decimals: int) -> str:
    if raw == 0:
        return "0"
    s = str(raw).zfill(decimals + 1)
    whole = s[:-decimals] if decimals > 0 else s
    frac = s[-decimals:] if decimals > 0 else ""
    frac = frac.rstrip("0") or "0" if frac else ""
    if frac == "0":
        return whole
    return f"{whole}.{frac}"


def _resolve_rpc_url(chain_key: str, rpc_override: str | None) -> str:
    """Determine the RPC URL: explicit override > env var > auto-derive from Alchemy key."""
    if rpc_override:
        return rpc_override

    env_url = os.environ.get("ALCHEMY_HTTP_URL", "").strip()
    if not env_url:
        print("error: ALCHEMY_HTTP_URL not set in env or .env", file=sys.stderr)
        sys.exit(2)

    if chain_key == "eth":
        return env_url

    # Auto-derive: swap the subdomain in the Alchemy URL
    chain_info = CHAINS[chain_key]
    target_subdomain = chain_info["alchemy_subdomain"]

    for known_sub in [c["alchemy_subdomain"] for c in CHAINS.values()]:
        if known_sub in env_url:
            return env_url.replace(known_sub, target_subdomain)

    # Fallback: try to extract the API key and build fresh
    if "alchemy.com/v2/" in env_url:
        api_key = env_url.split("/v2/")[-1]
        return f"https://{target_subdomain}.g.alchemy.com/v2/{api_key}"

    print(f"error: cannot derive {chain_key} RPC URL from ALCHEMY_HTTP_URL. Use --rpc.", file=sys.stderr)
    sys.exit(2)


def _erc20_balance(client: RpcClient, token_addr: str, wallet: str) -> int:
    calldata = "0x" + BALANCE_OF_SELECTOR + encode_address(wallet).hex()
    result = client.call("eth_call", [{"to": token_addr, "data": calldata}, "latest"])
    return int(result, 16) if result and result != "0x" else 0


def _erc20_decimals(client: RpcClient, token_addr: str) -> int | None:
    """Try to read decimals() from the contract. Returns None on failure."""
    try:
        result = client.call("eth_call", [{"to": token_addr, "data": "0x" + DECIMALS_SELECTOR}, "latest"])
        if result and result != "0x" and len(result) >= 66:
            return int(result, 16)
    except Exception:
        pass
    return None


def _get_eth_balance(client: RpcClient, wallet: str) -> int:
    result = client.call("eth_getBalance", [wallet, "latest"])
    return int(result, 16)


def _get_nonce(client: RpcClient, wallet: str) -> int:
    result = client.call("eth_getTransactionCount", [wallet, "latest"])
    return int(result, 16)


def _get_transfers(client: RpcClient, wallet: str, last_n: int) -> list[dict]:
    """Fetch recent transfers via Alchemy's alchemy_getAssetTransfers."""
    transfers: list[dict] = []

    for direction in ("from", "to"):
        params = {
            "fromBlock": "0x0",
            "toBlock": "latest",
            "category": ["external", "erc20", "erc721", "erc1155"],
            "withMetadata": True,
            "maxCount": hex(last_n),
            "order": "desc",
        }
        if direction == "from":
            params["fromAddress"] = wallet
        else:
            params["toAddress"] = wallet

        try:
            result = client.call("alchemy_getAssetTransfers", [params])
            transfers.extend(result.get("transfers", []))
        except Exception:
            pass

    transfers.sort(key=lambda t: t.get("metadata", {}).get("blockTimestamp", ""), reverse=True)
    return transfers[:last_n]


def _parse_token_arg(token_str: str) -> tuple[str, str, int | None]:
    """Parse --token NAME:ADDRESS or NAME:ADDRESS:DECIMALS"""
    parts = token_str.split(":")
    if len(parts) == 2:
        return parts[0], parts[1], None
    elif len(parts) == 3:
        return parts[0], parts[1], int(parts[2])
    else:
        print(f"error: invalid --token format: {token_str!r}", file=sys.stderr)
        print("  expected: NAME:0xADDRESS or NAME:0xADDRESS:DECIMALS", file=sys.stderr)
        sys.exit(2)


def _print_balances(client: RpcClient, wallet: str, chain_key: str,
                    extra_tokens: list[tuple[str, str, int | None]]) -> None:
    chain_info = CHAINS[chain_key]
    tokens = dict(chain_info["tokens"])

    # Resolve custom tokens (auto-fetch decimals if not provided)
    for name, addr, decimals in extra_tokens:
        if decimals is None:
            decimals = _erc20_decimals(client, addr)
            if decimals is None:
                print(f"  warning: could not fetch decimals for {name} ({addr}), assuming 18", file=sys.stderr)
                decimals = 18
        tokens[name] = {"address": addr, "decimals": decimals}

    print(f"\n{'='*60}")
    print(f"  Wallet:  {wallet}")
    print(f"  Chain:   {chain_info['name']}")
    print(f"  Explorer: {chain_info['explorer']}/address/{wallet}")
    print(f"{'='*60}\n")

    nonce = _get_nonce(client, wallet)
    print(f"  Nonce (tx count): {nonce}\n")
    print("  Token Balances:")
    print(f"  {'Token':<10} {'Balance':>28}")
    print(f"  {'-'*10} {'-'*28}")

    for symbol, info in tokens.items():
        if info["address"] is None:
            raw = _get_eth_balance(client, wallet)
        else:
            raw = _erc20_balance(client, info["address"], wallet)
        formatted = _format_units(raw, info["decimals"])
        if raw > 0:
            print(f"  {symbol:<10} {formatted:>28}  *")
        else:
            print(f"  {symbol:<10} {formatted:>28}")

    print()


def _print_transfers(transfers: list[dict], wallet: str, explorer_base: str) -> None:
    if not transfers:
        print("  No recent transfers found.\n")
        return

    print(f"  Recent Transfers (last {len(transfers)}):")
    print(f"  {'Dir':<5} {'Asset':<12} {'Value':>18} {'Counterparty':<44} {'Time'}")
    print(f"  {'-'*5} {'-'*12} {'-'*18} {'-'*44} {'-'*20}")

    for tx in transfers:
        from_addr = tx.get("from", "")
        to_addr = tx.get("to", "")
        is_outgoing = from_addr.lower() == wallet.lower()
        direction = "OUT" if is_outgoing else "IN"
        counterparty = to_addr if is_outgoing else from_addr

        asset = tx.get("asset") or "???"
        value = tx.get("value")
        value_str = f"{value:.6f}" if value is not None else "?"

        timestamp = tx.get("metadata", {}).get("blockTimestamp", "")
        if timestamp:
            try:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                time_str = dt.strftime("%Y-%m-%d %H:%M")
            except (ValueError, AttributeError):
                time_str = timestamp[:16]
        else:
            time_str = "—"

        if counterparty:
            cp_short = counterparty[:6] + "..." + counterparty[-4:]
        else:
            cp_short = "—"

        print(f"  {direction:<5} {asset:<12} {value_str:>18} {cp_short:<44} {time_str}")

    print()
    tx_hash = transfers[0].get("hash", "")
    if tx_hash:
        print(f"  Latest tx: {explorer_base}/tx/{tx_hash}")
    print()


def main(env_path: str | None = ".env") -> int:
    if env_path:
        _load_dotenv(env_path)

    parser = argparse.ArgumentParser(
        prog="wallet",
        description="Check wallet balances and recent transfers from a seed phrase.",
    )
    parser.add_argument("--prompt", action="store_true",
                        help="Securely prompt for mnemonic instead of reading from env")
    parser.add_argument("--skip-checksum", action="store_true",
                        help="Skip BIP-39 checksum validation (for non-standard mnemonics)")
    parser.add_argument("--chain", type=str, default="eth", choices=list(CHAINS.keys()),
                        help="Chain to query (default: eth). Options: eth, base, arb, opt")
    parser.add_argument("--rpc", type=str, default=None,
                        help="Override RPC URL (instead of deriving from ALCHEMY_HTTP_URL)")
    parser.add_argument("--token", type=str, action="append", default=[],
                        help="Custom token: NAME:0xADDRESS or NAME:0xADDRESS:DECIMALS (repeatable)")
    parser.add_argument("--last", type=int, default=20,
                        help="Number of recent transfers to show (default: 20)")
    parser.add_argument("--path", type=str, default=None,
                        help="HD derivation path (default: m/44'/60'/0'/0/0)")
    args = parser.parse_args()

    rpc_url = _resolve_rpc_url(args.chain, args.rpc)

    mnemonic = _resolve_mnemonic(args)
    hd_path = args.path or os.environ.get("HD_PATH", DEFAULT_HD_PATH).strip()

    try:
        seed = mnemonic_to_seed(mnemonic, skip_checksum=args.skip_checksum)
        priv = derive_path(seed, hd_path)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    wallet = derive_address(priv)
    del priv, seed, mnemonic

    extra_tokens = [_parse_token_arg(t) for t in args.token]
    client = RpcClient(rpc_url)
    chain_info = CHAINS[args.chain]

    try:
        _print_balances(client, wallet, args.chain, extra_tokens)
    except Exception as e:
        print(f"\n  error fetching balances: {e}", file=sys.stderr)
        return 1

    print("  Fetching transfer history...")
    try:
        transfers = _get_transfers(client, wallet, args.last)
        _print_transfers(transfers, wallet, chain_info["explorer"])
    except Exception as e:
        print(f"\n  error fetching transfers: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

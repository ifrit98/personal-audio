"""Curve 3pool (DAI/USDC/USDT) swap support.

Chosen over an aggregator because it needs no off-chain quote API: `get_dy`
prices the trade on-chain, so the minimum-output guard is computed from the
same source of truth that will execute the swap. One contract handles every
stable-to-stable leg, and there is no routing to get wrong.

Coin indices are verified against the live contract before any swap - see
`assert_coin_layout`. Selectors are recomputed from their signatures rather
than pasted in.
"""

from __future__ import annotations

from dataclasses import dataclass

from lib import abi
from lib.jsonrpc import RpcClient
from lib.keccak import keccak256

POOL = "0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7"

DAI = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
USDT = "0xdAC17F958D2ee523a2206206994597C13D831ec7"


@dataclass(frozen=True)
class Coin:
    key: str
    index: int
    address: str
    decimals: int


COINS: dict[str, Coin] = {
    "DAI": Coin("DAI", 0, DAI, 18),
    "USDC": Coin("USDC", 1, USDC, 6),
    "USDT": Coin("USDT", 2, USDT, 6),
}

# Curve reports `fee` scaled by 1e10 (1500000 == 0.015%).
FEE_DENOMINATOR = 10 ** 10


def _selector(signature: str) -> bytes:
    return keccak256(signature.encode("ascii"))[:4]


GET_DY_SELECTOR = _selector("get_dy(int128,int128,uint256)")
EXCHANGE_SELECTOR = _selector("exchange(int128,int128,uint256,uint256)")
COINS_SELECTOR = _selector("coins(uint256)")
FEE_SELECTOR = _selector("fee()")
BALANCES_SELECTOR = _selector("balances(uint256)")
APPROVE_SELECTOR = _selector("approve(address,uint256)")
ALLOWANCE_SELECTOR = _selector("allowance(address,address)")


# ---- calldata builders ----

def get_dy_calldata(i: int, j: int, dx: int) -> bytes:
    return (
        GET_DY_SELECTOR
        + abi.encode_int128(i)
        + abi.encode_int128(j)
        + abi.encode_uint256(dx)
    )


def exchange_calldata(i: int, j: int, dx: int, min_dy: int) -> bytes:
    return (
        EXCHANGE_SELECTOR
        + abi.encode_int128(i)
        + abi.encode_int128(j)
        + abi.encode_uint256(dx)
        + abi.encode_uint256(min_dy)
    )


def approve_calldata(spender: str, amount: int) -> bytes:
    return APPROVE_SELECTOR + abi.encode_address(spender) + abi.encode_uint256(amount)


def allowance_calldata(owner: str, spender: str) -> bytes:
    return ALLOWANCE_SELECTOR + abi.encode_address(owner) + abi.encode_address(spender)


# ---- reads ----

def _eth_call(client: RpcClient, to: str, data: bytes) -> bytes:
    out = client.call("eth_call", [{"to": to, "data": "0x" + data.hex()}, "latest"])
    if not isinstance(out, str) or not out.startswith("0x"):
        raise RuntimeError(f"unexpected eth_call return: {out!r}")
    return bytes.fromhex(out[2:])


def get_dy(client: RpcClient, i: int, j: int, dx: int) -> int:
    """Quote the swap on-chain. This is what min_dy should be derived from."""
    return abi.decode_uint256(_eth_call(client, POOL, get_dy_calldata(i, j, dx)))


def pool_coin(client: RpcClient, index: int) -> str:
    raw = _eth_call(client, POOL, COINS_SELECTOR + abi.encode_uint256(index))
    return abi.decode_address(raw)


def pool_fee(client: RpcClient) -> int:
    return abi.decode_uint256(_eth_call(client, POOL, FEE_SELECTOR))


def pool_balance(client: RpcClient, index: int) -> int:
    raw = _eth_call(client, POOL, BALANCES_SELECTOR + abi.encode_uint256(index))
    return abi.decode_uint256(raw)


def allowance(client: RpcClient, token: str, owner: str, spender: str) -> int:
    raw = _eth_call(client, token, allowance_calldata(owner, spender))
    return abi.decode_uint256(raw)


def assert_coin_layout(client: RpcClient) -> None:
    """Confirm the live pool's coin ordering matches our hardcoded indices.

    Cheap insurance against pointing a swap at the wrong asset: if Curve ever
    returns something unexpected at an index, we refuse rather than trade.
    """
    for coin in COINS.values():
        onchain = pool_coin(client, coin.index)
        if onchain.lower() != coin.address.lower():
            raise RuntimeError(
                f"Curve 3pool coin layout mismatch at index {coin.index}: "
                f"expected {coin.key} ({coin.address}), pool reports {onchain}. "
                "Refusing to swap."
            )


def min_dy_with_slippage(quoted_dy: int, slippage_bps: int) -> int:
    """Floor the acceptable output at `quoted_dy` minus a tolerance in basis points."""
    if slippage_bps < 0 or slippage_bps > 10_000:
        raise ValueError(f"slippage_bps out of range: {slippage_bps}")
    return quoted_dy * (10_000 - slippage_bps) // 10_000

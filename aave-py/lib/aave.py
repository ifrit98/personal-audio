"""Aave V3 mainnet constants, calldata builders, and read helpers."""

from __future__ import annotations

from dataclasses import dataclass

from lib import abi
from lib.eth import to_checksum_address
from lib.jsonrpc import RpcClient


# ---- canonical addresses (verified vs. bgd-labs/aave-address-book) ----

POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"


@dataclass(frozen=True)
class Market:
    key: str
    underlying: str
    a_token: str
    decimals: int


MARKETS: dict[str, Market] = {
    "DAI": Market(
        key="DAI",
        underlying="0x6B175474E89094C44Da98b954EedeAC495271d0F",
        a_token="0x018008bfb33d285247A21d44E50697654f754e63",
        decimals=18,
    ),
    "USDC": Market(
        key="USDC",
        underlying="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        a_token="0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c",
        decimals=6,
    ),
    "USDT": Market(
        key="USDT",
        underlying="0xdAC17F958D2ee523a2206206994597C13D831ec7",
        a_token="0x23878914EFE38d27C4D67Ab83ed1b93A74D4086a",
        decimals=6,
    ),
}

MARKET_ORDER: tuple[str, ...] = ("DAI", "USDC", "USDT")


# ---- selectors ----

WITHDRAW_SELECTOR = bytes.fromhex("69328dec")           # withdraw(address,uint256,address)
BALANCE_OF_SELECTOR = bytes.fromhex("70a08231")         # balanceOf(address)
DECIMALS_SELECTOR = bytes.fromhex("313ce567")           # decimals()
SYMBOL_SELECTOR = bytes.fromhex("95d89b41")             # symbol()
GET_RESERVE_DATA_SELECTOR = bytes.fromhex("35ea6a75")   # getReserveData(address)
GET_USER_ACCOUNT_DATA_SELECTOR = bytes.fromhex("bf92857c")  # getUserAccountData(address)


# ---- calldata builders ----

def withdraw_calldata(asset: str, amount: int, to: str) -> bytes:
    return abi.encode_withdraw_call(asset=asset, amount=amount, to=to)


def balance_of_calldata(holder: str) -> bytes:
    return BALANCE_OF_SELECTOR + abi.encode_address(holder)


def get_reserve_data_calldata(asset: str) -> bytes:
    return GET_RESERVE_DATA_SELECTOR + abi.encode_address(asset)


def get_user_account_data_calldata(user: str) -> bytes:
    return GET_USER_ACCOUNT_DATA_SELECTOR + abi.encode_address(user)


# ---- reserve status decoder ----

@dataclass(frozen=True)
class ReserveStatus:
    active: bool
    frozen: bool
    paused: bool


def decode_reserve_status(configuration: int) -> ReserveStatus:
    """Decode the packed uint256 ReserveConfigurationMap.

    Bit 56 = ACTIVE, 57 = FROZEN, 60 = PAUSED.
    See aave-v3-origin/src/contracts/protocol/libraries/configuration/ReserveConfiguration.sol.
    """
    return ReserveStatus(
        active=bool((configuration >> 56) & 1),
        frozen=bool((configuration >> 57) & 1),
        paused=bool((configuration >> 60) & 1),
    )


# ---- typed RPC reads ----

@dataclass(frozen=True)
class UserAccountData:
    total_collateral_base: int
    total_debt_base: int
    available_borrows_base: int
    current_liquidation_threshold: int
    ltv: int
    health_factor: int


def _eth_call(client: RpcClient, to: str, data: bytes, block: str = "latest") -> bytes:
    out = client.call(
        "eth_call",
        [{"to": to, "data": "0x" + data.hex()}, block],
    )
    if not isinstance(out, str) or not out.startswith("0x"):
        raise RuntimeError(f"unexpected eth_call return: {out!r}")
    return bytes.fromhex(out[2:])


def erc20_balance_of(client: RpcClient, token: str, holder: str) -> int:
    raw = _eth_call(client, token, balance_of_calldata(holder))
    return abi.decode_uint256(raw)


def erc20_decimals(client: RpcClient, token: str) -> int:
    raw = _eth_call(client, token, DECIMALS_SELECTOR)
    return abi.decode_uint8(raw)


def erc20_symbol(client: RpcClient, token: str) -> str:
    raw = _eth_call(client, token, SYMBOL_SELECTOR)
    return abi.decode_string(raw)


def get_reserve_configuration(client: RpcClient, asset: str) -> int:
    """Return the first field of getReserveData (the configuration uint256)."""
    raw = _eth_call(client, POOL, get_reserve_data_calldata(asset))
    # The struct's first member is `uint256 configuration` -> first 32 bytes.
    return abi.decode_uint256(raw[:32])


def get_user_account_data(client: RpcClient, user: str) -> UserAccountData:
    raw = _eth_call(client, POOL, get_user_account_data_calldata(user))
    if len(raw) < 32 * 6:
        raise RuntimeError(f"getUserAccountData return too short: {len(raw)} bytes")
    fields = [abi.decode_uint256(raw[i * 32:(i + 1) * 32]) for i in range(6)]
    return UserAccountData(*fields)


# Re-export
checksum = to_checksum_address

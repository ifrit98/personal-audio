"""Solidity ABI helpers for the exact shapes this tool needs.

We only ever encode (address, uint256, address) and we only ever decode
return values that are a single uint256 / address / boolean / string.
"""

from __future__ import annotations

WITHDRAW_SELECTOR = bytes.fromhex("69328dec")  # withdraw(address,uint256,address)


def _normalize_address(addr: str) -> bytes:
    if not isinstance(addr, str):
        raise TypeError("address must be a hex string")
    s = addr[2:] if addr.startswith(("0x", "0X")) else addr
    if len(s) != 40:
        raise ValueError(f"address must be 20 bytes / 40 hex chars, got {len(s)}")
    return bytes.fromhex(s)


def encode_uint256(value: int) -> bytes:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("uint256 must be a non-bool int")
    if value < 0 or value >= (1 << 256):
        raise ValueError(f"uint256 out of range: {value}")
    return value.to_bytes(32, "big")


def encode_address(addr: str) -> bytes:
    return b"\x00" * 12 + _normalize_address(addr)


def encode_withdraw_call(asset: str, amount: int, to: str) -> bytes:
    return (
        WITHDRAW_SELECTOR
        + encode_address(asset)
        + encode_uint256(amount)
        + encode_address(to)
    )


# ---- decoders for read-only eth_call returns ----

def decode_uint256(data: bytes) -> int:
    if len(data) != 32:
        raise ValueError(f"uint256 return must be 32 bytes, got {len(data)}")
    return int.from_bytes(data, "big")


def decode_address(data: bytes) -> str:
    if len(data) != 32:
        raise ValueError(f"address return must be 32 bytes, got {len(data)}")
    if data[:12] != b"\x00" * 12:
        raise ValueError("address return has non-zero high bits")
    return "0x" + data[12:].hex()


def decode_uint8(data: bytes) -> int:
    return decode_uint256(data) & 0xFF


def decode_string(data: bytes) -> str:
    """Decode the dynamic-string return (offset, length, payload)."""
    if len(data) < 64:
        raise ValueError("string return too short")
    offset = decode_uint256(data[:32])
    if offset != 32:
        raise ValueError(f"unexpected string offset: {offset}")
    length = decode_uint256(data[32:64])
    payload = data[64:64 + length]
    if len(payload) != length:
        raise ValueError("string payload truncated")
    return payload.decode("utf-8")

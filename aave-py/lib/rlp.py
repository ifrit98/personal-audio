"""Minimal RLP encoder for the EIP-1559 envelope.

Accepts bytes, ints (encoded as minimal big-endian; 0 -> b""), and lists
of the same. No decoding (we only sign/broadcast).

Reference: Ethereum yellow paper, Appendix B.
"""

from __future__ import annotations


def encode(item: bytes | int | list) -> bytes:
    if isinstance(item, int):
        if item < 0:
            raise ValueError("RLP cannot encode negative integers")
        if item == 0:
            return _encode_bytes(b"")
        return _encode_bytes(item.to_bytes((item.bit_length() + 7) // 8, "big"))
    if isinstance(item, (bytes, bytearray)):
        return _encode_bytes(bytes(item))
    if isinstance(item, list):
        return _encode_list(item)
    raise TypeError(f"RLP cannot encode {type(item).__name__}")


def _encode_bytes(b: bytes) -> bytes:
    if len(b) == 1 and b[0] < 0x80:
        return b
    return _length_prefix(len(b), 0x80) + b


def _encode_list(lst: list) -> bytes:
    payload = b"".join(encode(x) for x in lst)
    return _length_prefix(len(payload), 0xC0) + payload


def _length_prefix(length: int, base: int) -> bytes:
    if length < 56:
        return bytes([base + length])
    enc = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([base + 55 + len(enc)]) + enc

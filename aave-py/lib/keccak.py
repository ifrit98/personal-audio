"""Pure-Python Keccak-256 (the pre-FIPS-202 variant Ethereum uses).

Differs from hashlib.sha3_256 only in the padding byte: 0x01 here vs. 0x06
in NIST's later SHA-3 standardization.

Reference: https://keccak.team/keccak_specs_summary.html
"""

from __future__ import annotations

_RATE_BYTES = 136  # Keccak-256: r=1088 bits, c=512 bits


def _rot(x: int, n: int) -> int:
    return ((x << n) | (x >> (64 - n))) & 0xFFFFFFFFFFFFFFFF


_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]

_R = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]


def _keccak_f1600(state: list[list[int]]) -> None:
    for rc in _RC:
        # theta
        c = [state[x][0] ^ state[x][1] ^ state[x][2] ^ state[x][3] ^ state[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rot(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x][y] ^= d[x]
        # rho + pi
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rot(state[x][y], _R[x][y])
        # chi
        for x in range(5):
            for y in range(5):
                state[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y]) & 0xFFFFFFFFFFFFFFFF
        # iota
        state[0][0] ^= rc


def keccak256(data: bytes) -> bytes:
    """Keccak-256 hash with the original (pre-NIST) padding byte 0x01."""
    state = [[0] * 5 for _ in range(5)]
    # absorb full rate-sized blocks
    offset = 0
    n = len(data)
    while n - offset >= _RATE_BYTES:
        _absorb_block(state, data[offset:offset + _RATE_BYTES])
        offset += _RATE_BYTES
    # pad final block: 0x01 ... 0x80 (multi-rate padding "10*1")
    tail = bytearray(data[offset:])
    tail.append(0x01)
    tail.extend(b"\x00" * (_RATE_BYTES - len(tail) - 1))
    tail.append(0x80)
    _absorb_block(state, bytes(tail))
    # squeeze 32 bytes (one rate-sized squeeze is enough for 256 bits)
    out = bytearray()
    for y in range(5):
        for x in range(5):
            if len(out) >= 32:
                break
            out.extend(state[x][y].to_bytes(8, "little"))
    return bytes(out[:32])


def _absorb_block(state: list[list[int]], block: bytes) -> None:
    assert len(block) == _RATE_BYTES
    for i in range(_RATE_BYTES // 8):
        x = i % 5
        y = i // 5
        word = int.from_bytes(block[i * 8:(i + 1) * 8], "little")
        state[x][y] ^= word
    _keccak_f1600(state)

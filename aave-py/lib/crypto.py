"""Thin coincurve adapter.

The only place this codebase calls into a C extension. Everything else
above is pure Python.
"""

from __future__ import annotations

from coincurve import PrivateKey

from lib.keccak import keccak256


def sign_recoverable(priv32: bytes, sighash32: bytes) -> tuple[int, int, int]:
    """Return (r, s, y_parity) for an EIP-1559 signature.

    coincurve guarantees a low-s canonical form per BIP-62.
    """
    if len(priv32) != 32:
        raise ValueError(f"priv key must be 32 bytes, got {len(priv32)}")
    if len(sighash32) != 32:
        raise ValueError(f"sighash must be 32 bytes, got {len(sighash32)}")
    sig = PrivateKey(priv32).sign_recoverable(sighash32, hasher=None)
    r = int.from_bytes(sig[0:32], "big")
    s = int.from_bytes(sig[32:64], "big")
    y = sig[64]
    if y not in (0, 1):
        raise RuntimeError(f"unexpected recovery byte: {y}")
    return r, s, y


def derive_address(priv32: bytes) -> str:
    """Return EIP-55 checksummed Ethereum address for the given private key."""
    if len(priv32) != 32:
        raise ValueError(f"priv key must be 32 bytes, got {len(priv32)}")
    pub = PrivateKey(priv32).public_key.format(compressed=False)
    # Drop the leading 0x04 prefix, hash the X||Y bytes, take the last 20.
    addr_bytes = keccak256(pub[1:])[12:]
    return _to_eip55(addr_bytes)


def _to_eip55(addr_bytes: bytes) -> str:
    addr_hex = addr_bytes.hex()
    h = keccak256(addr_hex.encode("ascii")).hex()
    out = ["0x"]
    for i, c in enumerate(addr_hex):
        if c in "0123456789":
            out.append(c)
        else:
            out.append(c.upper() if int(h[i], 16) >= 8 else c)
    return "".join(out)

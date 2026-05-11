"""BIP-39 mnemonic -> seed and BIP-32 HD derivation.

Only supports private-key derivation (CKDpriv); we never need public-only
derivation. Hardened indices are encoded with the high bit set per BIP-32.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

from coincurve import PrivateKey

_WORDLIST_PATH = Path(__file__).parent.parent / "wordlist.txt"
_SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def _load_wordlist() -> list[str]:
    return _WORDLIST_PATH.read_text(encoding="utf-8").splitlines()


_WORDS: list[str] | None = None


def _words() -> list[str]:
    global _WORDS
    if _WORDS is None:
        _WORDS = _load_wordlist()
        if len(_WORDS) != 2048:
            raise RuntimeError(f"wordlist.txt corrupt: {len(_WORDS)} entries (expected 2048)")
    return _WORDS


def mnemonic_to_seed(mnemonic: str, passphrase: str = "") -> bytes:
    """Per BIP-39: validate the mnemonic, then PBKDF2-HMAC-SHA512."""
    _validate_mnemonic(mnemonic)
    salt = ("mnemonic" + passphrase).encode("utf-8")
    return hashlib.pbkdf2_hmac(
        "sha512",
        mnemonic.encode("utf-8"),
        salt,
        2048,
        dklen=64,
    )


def _validate_mnemonic(mnemonic: str) -> None:
    words = mnemonic.strip().split()
    if len(words) not in (12, 15, 18, 21, 24):
        raise ValueError(f"mnemonic must be 12/15/18/21/24 words, got {len(words)}")
    wordlist = _words()
    index_of = {w: i for i, w in enumerate(wordlist)}
    indices: list[int] = []
    for w in words:
        if w not in index_of:
            raise ValueError(f"word not in BIP-39 wordlist: {w!r}")
        indices.append(index_of[w])
    bits = "".join(f"{i:011b}" for i in indices)
    cs_len = len(words) // 3  # 4..8 bits
    ent_len = len(bits) - cs_len
    ent_bits = bits[:ent_len]
    cs_bits = bits[ent_len:]
    ent_bytes = int(ent_bits, 2).to_bytes(ent_len // 8, "big")
    h = hashlib.sha256(ent_bytes).digest()
    expected_cs = "".join(f"{b:08b}" for b in h)[:cs_len]
    if cs_bits != expected_cs:
        raise ValueError("mnemonic checksum mismatch")


def _master_key(seed: bytes) -> tuple[bytes, bytes]:
    h = hmac.new(b"Bitcoin seed", seed, hashlib.sha512).digest()
    return h[:32], h[32:]


def _ckd_priv(parent_priv: bytes, parent_cc: bytes, index: int) -> tuple[bytes, bytes]:
    if index >= 1 << 31:  # hardened
        data = b"\x00" + parent_priv + index.to_bytes(4, "big")
    else:
        pub = PrivateKey(parent_priv).public_key.format(compressed=True)
        data = pub + index.to_bytes(4, "big")
    h = hmac.new(parent_cc, data, hashlib.sha512).digest()
    il = int.from_bytes(h[:32], "big")
    if il >= _SECP256K1_N:
        raise ValueError("BIP-32 derivation produced il >= n; try the next index")
    parent_int = int.from_bytes(parent_priv, "big")
    child_int = (il + parent_int) % _SECP256K1_N
    if child_int == 0:
        raise ValueError("BIP-32 derivation produced zero key; try the next index")
    return child_int.to_bytes(32, "big"), h[32:]


def derive_path(seed: bytes, path: str) -> bytes:
    """Derive the 32-byte private key at the given BIP-32 path (e.g. m/44'/60'/0'/0/0)."""
    if not path.startswith("m"):
        raise ValueError("path must start with 'm'")
    parts = path.split("/")
    if parts[0] != "m":
        raise ValueError("path must start with 'm'")
    priv, cc = _master_key(seed)
    for p in parts[1:]:
        if p == "":
            continue
        hardened = p.endswith("'") or p.endswith("h") or p.endswith("H")
        num_str = p.rstrip("'hH")
        if not num_str.isdigit():
            raise ValueError(f"invalid path component: {p!r}")
        idx = int(num_str)
        if idx < 0 or idx >= 1 << 31:
            raise ValueError(f"index out of range: {idx}")
        if hardened:
            idx += 1 << 31
        priv, cc = _ckd_priv(priv, cc, idx)
    return priv

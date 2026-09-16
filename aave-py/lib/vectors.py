"""Curated, minimal test vectors used by the `selftest` subcommand.

These are a subset of the unit-test vectors in tests/, sufficient to detect
any wholesale failure in our hand-rolled encoders. If any of these fail,
the CLI refuses to sign anything.
"""

from __future__ import annotations

from coincurve import PublicKey

from lib import abi, rlp
from lib.bip39_32 import derive_path, mnemonic_to_seed
from lib.crypto import derive_address, sign_recoverable
from lib.eth import (
    build_eip1559_tx,
    sighash_eip1559,
    sign_eip1559_tx,
    to_checksum_address,
)
from lib.keccak import keccak256


# Wallet derived from the BIP-39 canonical "abandon x11 about" mnemonic at
# m/44'/60'/0'/0/0. Cross-verified against MetaMask, ethers.js, web3.py, and
# eth-account.
ABANDON_PRIV = bytes.fromhex(
    "1ab42cc412b618bdea3a599e3c9bae199ebf030895b039e9db1e30dafb12b727"
)
ABANDON_ADDR = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"
ABANDON_MNEMONIC = (
    "abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon about"
)


def _check_keccak() -> tuple[bool, str | None]:
    cases = [
        (b"", "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"),
        (b"abc", "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"),
    ]
    for inp, expected in cases:
        got = keccak256(inp).hex()
        if got != expected:
            return False, f"keccak256({inp!r}) -> {got}, expected {expected}"
    return True, None


def _check_rlp() -> tuple[bool, str | None]:
    cases: list[tuple[object, str]] = [
        (b"", "80"),
        (b"dog", "83646f67"),
        ([], "c0"),
        ([b"cat", b"dog"], "c88363617483646f67"),
        (1024, "820400"),
    ]
    for inp, expected in cases:
        got = rlp.encode(inp).hex()
        if got != expected:
            return False, f"rlp({inp!r}) -> {got}, expected {expected}"
    return True, None


def _check_abi() -> tuple[bool, str | None]:
    data = abi.encode_withdraw_call(
        asset="0x6B175474E89094C44Da98b954EedeAC495271d0F",
        amount=(1 << 256) - 1,
        to=ABANDON_ADDR,
    )
    expected_prefix = (
        "69328dec"
        + "0000000000000000000000006b175474e89094c44da98b954eedeac495271d0f"
        + "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        + "0000000000000000000000009858effd232b4033e47d90003d41ec34ecaeda94"
    )
    got = data.hex()
    if got != expected_prefix:
        return False, f"withdraw calldata mismatch: {got}"
    return True, None


def _check_bip39() -> tuple[bool, str | None]:
    seed = mnemonic_to_seed(ABANDON_MNEMONIC)
    if seed.hex() != (
        "5eb00bbddcf069084889a8ab9155568165f5c453ccb85e70811aaed6f6da5fc1"
        "9a5ac40b389cd370d086206dec8aa6c43daea6690f20ad3d8d48b2d2ce9e38e4"
    ):
        return False, f"BIP-39 abandon seed mismatch: {seed.hex()}"
    return True, None


def _check_bip32() -> tuple[bool, str | None]:
    seed = mnemonic_to_seed(ABANDON_MNEMONIC)
    priv = derive_path(seed, "m/44'/60'/0'/0/0")
    if priv != ABANDON_PRIV:
        return False, f"BIP-32 abandon path mismatch: {priv.hex()}"
    return True, None


def _check_eip55() -> tuple[bool, str | None]:
    cases = [
        ("0xfb6916095ca1df60bb79ce92ce3ea74c37c5d359", "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359"),
        ("0x52908400098527886e0f7030069857d2e4169ee7", "0x52908400098527886E0F7030069857D2E4169EE7"),
    ]
    for inp, expected in cases:
        got = to_checksum_address(inp)
        if got != expected:
            return False, f"EIP-55({inp}) -> {got}, expected {expected}"
    if derive_address(ABANDON_PRIV) != ABANDON_ADDR:
        return False, "derive_address(ABANDON_PRIV) mismatch"
    return True, None


def _check_eip1559() -> tuple[bool, str | None]:
    tx = build_eip1559_tx(
        chain_id=1,
        nonce=42,
        max_priority_fee_per_gas=1_500_000_000,
        max_fee_per_gas=20_000_000_000,
        gas_limit=500_000,
        to="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
        value=0,
        data=bytes.fromhex("69328dec"),
    )
    sighash = sighash_eip1559(tx)
    raw = sign_eip1559_tx(tx, ABANDON_PRIV)
    if raw[0] != 0x02:
        return False, f"raw tx must start with 0x02, got {raw[0]:#x}"
    # Round-trip recovery
    r, s, y = sign_recoverable(ABANDON_PRIV, sighash)
    sig65 = r.to_bytes(32, "big") + s.to_bytes(32, "big") + bytes([y])
    pub = PublicKey.from_signature_and_message(sig65, sighash, hasher=None)
    addr = "0x" + keccak256(pub.format(compressed=False)[1:])[12:].hex()
    if addr.lower() != ABANDON_ADDR.lower():
        return False, f"recovered addr {addr} != {ABANDON_ADDR}"
    return True, None


_CHECKS = [
    ("keccak", _check_keccak),
    ("rlp", _check_rlp),
    ("abi", _check_abi),
    ("bip39", _check_bip39),
    ("bip32", _check_bip32),
    ("eip55", _check_eip55),
    ("eip1559", _check_eip1559),
]


def run_all() -> tuple[bool, list[tuple[str, bool, str | None]]]:
    results: list[tuple[str, bool, str | None]] = []
    overall = True
    for name, fn in _CHECKS:
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"raised {type(e).__name__}: {e}"
        if not ok:
            overall = False
        results.append((name, ok, detail))
    return overall, results

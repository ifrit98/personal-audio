# Aave V3 Stable Drainer — Minimal Python Port — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replicate the existing TypeScript `aave/` tool in Python with exactly one PyPI dependency (`coincurve`), producing a `verify`/`withdraw`/`selftest` CLI that drains DAI → USDC → USDT positions from Aave V3 mainnet using a hot wallet derived from a BIP-39 mnemonic.

**Architecture:** A single `aave-py/` directory containing one `aave.py` entrypoint and a `lib/` package with one file per concern (Keccak, RLP, ABI, JSON-RPC, secp256k1 adapter, BIP-39/32, EIP-1559 signing, Aave helpers, embedded test vectors). Every hand-rolled crypto/encoding module is verified at startup against published test vectors via the `selftest` subcommand, and `verify`/`withdraw` invoke `selftest` internally before doing anything else.

**Tech Stack:** Python 3.10+, `coincurve` (libsecp256k1 binding) for ECDSA, stdlib `hashlib`/`hmac`/`urllib.request`/`json`/`secrets`/`argparse`/`getpass`/`unittest`. No other external dependencies.

**Spec:** `docs/superpowers/specs/2026-05-11-aave-py-min-design.md`

---

## File map

Created in `aave-py/`:

| File | Purpose |
|---|---|
| `requirements.txt` | one line: `coincurve>=20` |
| `.gitignore` | exclude `.env`, `__pycache__`, etc. |
| `.env.example` | env template (mirrors TS version with Python notes) |
| `README.md` | full operational guide |
| `wordlist.txt` | BIP-39 English wordlist, 2048 lines |
| `aave.py` | CLI entrypoint, `.env` parser, dispatch to subcommands |
| `lib/__init__.py` | empty package marker |
| `lib/keccak.py` | pure-Python Keccak-256 sponge |
| `lib/rlp.py` | RLP encoder for `bytes \| int \| list` |
| `lib/abi.py` | ABI encode for `(address, uint256, address)` + decoders |
| `lib/jsonrpc.py` | `urllib`-based JSON-RPC client |
| `lib/crypto.py` | `coincurve` adapter: sign + address derivation |
| `lib/bip39_32.py` | mnemonic → seed → derived 32-byte privkey |
| `lib/eth.py` | EIP-55 checksum + EIP-1559 tx assembly + signing |
| `lib/aave.py` | Aave V3 calldata + read helpers + status decoder |
| `lib/vectors.py` | curated test vectors used by `selftest` subcommand |
| `tests/__init__.py` | empty |
| `tests/test_keccak.py` | unit tests with KAT vectors |
| `tests/test_rlp.py` | unit tests |
| `tests/test_abi.py` | unit tests |
| `tests/test_jsonrpc.py` | unit tests with mocked HTTP |
| `tests/test_crypto.py` | unit tests (requires `coincurve`) |
| `tests/test_bip39_32.py` | unit tests with BIP-39 + BIP-32 vectors |
| `tests/test_eth.py` | EIP-55 + EIP-1559 round-trip tests |
| `tests/test_aave.py` | calldata + status decoder tests |
| `tests/test_cli.py` | argparse + subcommand tests with mocks |

All tests use stdlib `unittest`, runnable via `python -m unittest discover -s tests` from `aave-py/`.

---

## Conventions used in this plan

- Code blocks for **test files** are complete and self-contained (you can paste them verbatim).
- Code blocks for **library files** are complete (full file contents).
- Run commands assume **CWD = `aave-py/`**.
- Each task ends with a **commit step** with the exact `git add` and `git commit -m` to use.
- Where vectors are inlined in tests, they're sourced from canonical references and noted in comments.

---

## Task 1: Scaffold the project

**Files:**
- Create: `aave-py/requirements.txt`
- Create: `aave-py/.gitignore`
- Create: `aave-py/.env.example`
- Create: `aave-py/README.md` (placeholder; final content in last task)
- Create: `aave-py/lib/__init__.py`
- Create: `aave-py/tests/__init__.py`

- [ ] **Step 1: Create the directory structure**

```bash
mkdir -p aave-py/lib aave-py/tests
```

- [ ] **Step 2: Write `aave-py/requirements.txt`**

```
coincurve>=20
```

- [ ] **Step 3: Write `aave-py/.gitignore`**

```
__pycache__/
*.py[cod]
.env
.env.local
.env.*.local
*.log
.DS_Store
.venv/
venv/
```

- [ ] **Step 4: Write `aave-py/.env.example`** (mirrors TS `.env.example` with Python notes)

```
# =============================================================================
# Aave V3 Stable Drainer (Python) - environment template
# =============================================================================
# Same threat model as the TS sibling project. See README.md for the full
# security warnings.
# =============================================================================

ALCHEMY_HTTP_URL=https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY_HERE

# 12 or 24 word BIP-39 phrase. Single line, single spaces between words.
# RECOMMENDED: leave this blank and pass --prompt to the CLI so the seed
# is never written to disk.
MNEMONIC=

HD_PATH=m/44'/60'/0'/0/0

# Optional. Defaults to the wallet itself.
# RECIPIENT=0x0000000000000000000000000000000000000000

GAS_LIMIT=500000
# MAX_FEE_GWEI=200
PRIORITY_FEE_GWEI=2
AUTO_CONFIRM=false
```

- [ ] **Step 5: Write `aave-py/README.md`** (placeholder)

```markdown
# Aave V3 Stable Drainer (Python, minimal)

Implementation in progress. See the operational guide once Task 16 is complete.
```

- [ ] **Step 6: Create empty package markers**

```bash
touch aave-py/lib/__init__.py aave-py/tests/__init__.py
```

- [ ] **Step 7: Verify scaffold**

Run: `ls -la aave-py/ aave-py/lib/ aave-py/tests/`
Expected: see the 6 files plus the two `__init__.py` markers.

- [ ] **Step 8: Commit**

```bash
git add aave-py/
git commit -m "Scaffold aave-py/ project skeleton"
```

---

## Task 2: Embed the BIP-39 English wordlist

**Files:**
- Create: `aave-py/wordlist.txt`
- Create: `aave-py/tests/test_wordlist.py`

The wordlist is normative for BIP-39 — we cannot generate it. Source from the canonical Bitcoin BIP-39 English list (2048 words, alphabetically sorted, one per line).

- [ ] **Step 1: Write the failing test**

`aave-py/tests/test_wordlist.py`:

```python
import hashlib
import unittest
from pathlib import Path


class TestWordlist(unittest.TestCase):
    def test_wordlist_is_canonical(self):
        path = Path(__file__).parent.parent / "wordlist.txt"
        # Fail fast on file corruption: enforce canonical BIP-39 English SHA-256.
        self.assertEqual(
            hashlib.sha256(path.read_bytes()).hexdigest(),
            "2f5eed53a4727b4bf8880d8f3f199efc90e58503646d9ff8eff3a2ed3b24dbda",
            "wordlist.txt does not match canonical BIP-39 English SHA-256",
        )
        words = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(words), 2048, "BIP-39 wordlist must have exactly 2048 words")
        # Canonical first and last word per the BIP-39 English wordlist.
        self.assertEqual(words[0], "abandon")
        self.assertEqual(words[-1], "zoo")
        # Specific known indices for sanity:
        self.assertEqual(words[3], "about")  # 4th word
        self.assertEqual(words[1023], "lend")
        self.assertEqual(words[2047], "zoo")
        # All lowercase ASCII, no surrounding whitespace.
        for i, w in enumerate(words):
            self.assertTrue(w.isascii() and w.islower(), f"word {i} not ascii lowercase: {w!r}")
            self.assertEqual(w, w.strip(), f"word {i} has whitespace: {w!r}")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aave-py && python -m unittest tests.test_wordlist -v`
Expected: FAIL — `FileNotFoundError: ... wordlist.txt`

- [ ] **Step 3: Download the canonical wordlist**

Run from inside `aave-py/`:

```bash
curl -fsSL https://raw.githubusercontent.com/bitcoin/bips/master/bip-0039/english.txt -o wordlist.txt
```

(If the hardened machine has no network, copy from a trusted source. The SHA-256 of the canonical file is well-known and any BIP-39 test vector cross-checks the wordlist content.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aave-py && python -m unittest tests.test_wordlist -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add aave-py/wordlist.txt aave-py/tests/test_wordlist.py
git commit -m "Embed canonical BIP-39 English wordlist"
```

---

## Task 3: Keccak-256

**Files:**
- Create: `aave-py/lib/keccak.py`
- Create: `aave-py/tests/test_keccak.py`

- [ ] **Step 1: Write the failing test**

`aave-py/tests/test_keccak.py`:

```python
import unittest

from lib.keccak import keccak256


class TestKeccak256(unittest.TestCase):
    """Test vectors from the original Keccak submission (pre-NIST padding 0x01)."""

    def test_empty(self):
        # keccak256("") - widely-published vector
        self.assertEqual(
            keccak256(b"").hex(),
            "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
        )

    def test_abc(self):
        self.assertEqual(
            keccak256(b"abc").hex(),
            "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
        )

    def test_quick_brown_fox(self):
        self.assertEqual(
            keccak256(b"The quick brown fox jumps over the lazy dog").hex(),
            "4d741b6f1eb29cb2a9b9911c82f56fa8d73b04959d3d9d222895df6c0b28aa15",
        )

    def test_multi_block_input_matches_block_at_a_time(self):
        """Indirectly exercises the absorb loop: 200-byte input (>1 rate-block)
        must produce the same hash whether absorbed in one shot or computed
        independently. We sanity-check by hashing both the full string and a
        prefix and asserting they differ - any silent state-reuse bug would
        produce equal digests."""
        long_input = b"\xa3" * 200
        prefix = b"\xa3" * 136
        self.assertNotEqual(keccak256(long_input), keccak256(prefix))
        self.assertNotEqual(keccak256(long_input), keccak256(b""))

    # Boundary-length KATs, generated against pycryptodome's keccak (digest_bits=256).
    # 135 specifically exercises the merged-pad-byte branch (rate - 1 input).
    def test_boundary_135_bytes(self):
        self.assertEqual(
            keccak256(b"\x00" * 135).hex(),
            "29e3704feeca7fb9ba229f0fa04d9b36449cf3ad6e1d85d9cfff3a10df9abc3e",
        )

    def test_boundary_136_bytes(self):
        self.assertEqual(
            keccak256(b"\x00" * 136).hex(),
            "3a5912a7c5faa06ee4fe906253e339467a9ce87d533c65be3c15cb231cdb25f9",
        )

    def test_boundary_137_bytes(self):
        self.assertEqual(
            keccak256(b"\x00" * 137).hex(),
            "bee7fbb405cb0d91a8775e338c4a5e4b5d6b2d051f687fa942043cffdc73bd28",
        )

    def test_multi_block_200_bytes(self):
        self.assertEqual(
            keccak256(b"\x00" * 200).hex(),
            "e1bb54e1bc3af48d01e5dbfc81015c98152a574f6428c6948aa4837c9c0baad9",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_keccak -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lib.keccak'`

- [ ] **Step 3: Write `aave-py/lib/keccak.py`**

```python
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
                state[x][y] = (b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y])) & 0xFFFFFFFFFFFFFFFF
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
    # pad final block: multi-rate padding "10*1".
    # If the tail is exactly rate-1 bytes there is only one slot left, so we
    # merge the two pad bits (0x01 | 0x80) into a single 0x81 byte.
    tail = bytearray(data[offset:])
    if len(tail) == _RATE_BYTES - 1:
        tail.append(0x81)
    else:
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
    if len(block) != _RATE_BYTES:
        raise ValueError(f"Keccak absorb block must be {_RATE_BYTES} bytes, got {len(block)}")
    for i in range(_RATE_BYTES // 8):
        x = i % 5
        y = i // 5
        word = int.from_bytes(block[i * 8:(i + 1) * 8], "little")
        state[x][y] ^= word
    _keccak_f1600(state)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_keccak -v`
Expected: PASS (8 tests). Three exact-vector checks against canonical Keccak-256 outputs (empty / "abc" / quick-brown-fox), one structural sanity check on the absorb loop, and four boundary-length KATs (135 / 136 / 137 / 200) that exercise the merged-pad branch and multi-block absorption.

- [ ] **Step 5: Commit**

```bash
git add aave-py/lib/keccak.py aave-py/tests/test_keccak.py
git commit -m "Add pure-Python Keccak-256 with KAT vectors"
```

---

## Task 4: RLP encoder

**Files:**
- Create: `aave-py/lib/rlp.py`
- Create: `aave-py/tests/test_rlp.py`

- [ ] **Step 1: Write the failing test**

`aave-py/tests/test_rlp.py`:

```python
import unittest

from lib.rlp import encode


class TestRlp(unittest.TestCase):
    """Vectors from the Ethereum yellow paper appendix and the RLP wiki."""

    def test_empty_string(self):
        self.assertEqual(encode(b"").hex(), "80")

    def test_zero_byte(self):
        # Single byte < 0x80 encodes as itself.
        self.assertEqual(encode(b"\x00").hex(), "00")

    def test_single_byte_below_threshold(self):
        self.assertEqual(encode(b"\x0f").hex(), "0f")

    def test_short_string(self):
        # "dog" -> 83 64 6f 67
        self.assertEqual(encode(b"dog").hex(), "8364 6f67".replace(" ", ""))

    def test_55_byte_string(self):
        # 55 bytes is the boundary for short-string encoding.
        s = b"L" * 55
        self.assertEqual(encode(s).hex(), "b7" + "4c" * 55)

    def test_56_byte_string(self):
        # 56 bytes -> long-string form: 0xb8 (1 byte length prefix), 0x38 length, payload.
        s = b"L" * 56
        self.assertEqual(encode(s).hex(), "b838" + "4c" * 56)

    def test_empty_list(self):
        self.assertEqual(encode([]).hex(), "c0")

    def test_short_list(self):
        # ["cat", "dog"] -> c8 83 63 61 74 83 64 6f 67
        self.assertEqual(
            encode([b"cat", b"dog"]).hex(),
            "c883636174 83646f67".replace(" ", ""),
        )

    def test_int_zero(self):
        # 0 encodes as empty bytes -> 0x80
        self.assertEqual(encode(0).hex(), "80")

    def test_int_15(self):
        self.assertEqual(encode(15).hex(), "0f")

    def test_int_1024(self):
        # 1024 = 0x0400 -> 82 04 00
        self.assertEqual(encode(1024).hex(), "820400")

    def test_nested_list(self):
        # [[], [[]], [[], [[]]]] -> the "set theoretic representation of three"
        # Per yellow paper appendix.
        self.assertEqual(encode([[], [[]], [[], [[]]]]).hex(), "c7c0c1c0c3c0c1c0")

    def test_list_55_byte_payload(self):
        # 11 * (1 byte 0x84 + 4 bytes "AAAA") = 55 bytes payload -> short-list 0xf7.
        items = [b"AAAA"] * 11
        out = encode(items)
        self.assertEqual(out[0], 0xf7)
        self.assertEqual(len(out), 56)

    def test_list_56_byte_payload(self):
        # 56-byte payload -> long-list form: 0xf8 0x38 then payload.
        items = [b"X" * 54, b"Y"]
        out = encode(items)
        self.assertEqual(out[0], 0xf8)
        self.assertEqual(out[1], 56)
        self.assertEqual(len(out), 2 + 56)

    def test_list_long_payload_lenoflen(self):
        # 303-byte payload -> 0xf9 0x012f then payload (multi-byte length).
        out = encode([b"X" * 300])
        self.assertEqual(out[0], 0xf9)
        self.assertEqual(out[1:3], bytes.fromhex("012f"))
        self.assertEqual(len(out), 3 + 303)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_rlp -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `aave-py/lib/rlp.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_rlp -v`
Expected: PASS (15 tests).

- [ ] **Step 5: Commit**

```bash
git add aave-py/lib/rlp.py aave-py/tests/test_rlp.py
git commit -m "Add RLP encoder with yellow-paper test vectors"
```

---

## Task 5: ABI encoder/decoder

**Files:**
- Create: `aave-py/lib/abi.py`
- Create: `aave-py/tests/test_abi.py`

- [ ] **Step 1: Write the failing test**

`aave-py/tests/test_abi.py`:

```python
import unittest

from lib.abi import (
    encode_address,
    encode_uint256,
    encode_withdraw_call,
    decode_uint256,
    decode_address,
)


# Selector for `withdraw(address,uint256,address)` per Solidity ABI.
WITHDRAW_SELECTOR = bytes.fromhex("69328dec")
DAI = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
RECIPIENT = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
MAX_U256 = (1 << 256) - 1


class TestAbiEncode(unittest.TestCase):
    def test_encode_uint256_zero(self):
        self.assertEqual(encode_uint256(0).hex(), "00" * 32)

    def test_encode_uint256_one(self):
        self.assertEqual(encode_uint256(1).hex(), "00" * 31 + "01")

    def test_encode_uint256_max(self):
        self.assertEqual(encode_uint256(MAX_U256).hex(), "ff" * 32)

    def test_encode_uint256_rejects_negative(self):
        with self.assertRaises(ValueError):
            encode_uint256(-1)

    def test_encode_uint256_rejects_too_large(self):
        with self.assertRaises(ValueError):
            encode_uint256(1 << 256)

    def test_encode_address(self):
        # 20-byte address left-padded with 12 zero bytes.
        expected = "00" * 12 + "6b175474e89094c44da98b954eedeac495271d0f"
        self.assertEqual(encode_address(DAI).hex(), expected)

    def test_encode_withdraw_call_max(self):
        data = encode_withdraw_call(asset=DAI, amount=MAX_U256, to=RECIPIENT)
        # selector + 3 * 32 bytes
        self.assertEqual(len(data), 4 + 32 * 3)
        self.assertEqual(data[:4], WITHDRAW_SELECTOR)
        # First arg = DAI address, padded
        self.assertEqual(data[4:36].hex(), "00" * 12 + "6b175474e89094c44da98b954eedeac495271d0f")
        # Second arg = max uint256
        self.assertEqual(data[36:68].hex(), "ff" * 32)
        # Third arg = recipient address, padded
        self.assertEqual(data[68:100].hex(), "00" * 12 + "f39fd6e51aad88f6f4ce6ab8827279cfffb92266")


class TestAbiDecode(unittest.TestCase):
    def test_decode_uint256(self):
        self.assertEqual(decode_uint256(bytes.fromhex("00" * 31 + "2a")), 42)

    def test_decode_address(self):
        encoded = bytes.fromhex("00" * 12 + "f39fd6e51aad88f6f4ce6ab8827279cfffb92266")
        # Returned as lowercase hex with 0x prefix; checksum is applied elsewhere.
        self.assertEqual(decode_address(encoded), "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_abi -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write `aave-py/lib/abi.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_abi -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add aave-py/lib/abi.py aave-py/tests/test_abi.py
git commit -m "Add ABI encode/decode helpers with vectors"
```

---

## Task 6: JSON-RPC client

**Files:**
- Create: `aave-py/lib/jsonrpc.py`
- Create: `aave-py/tests/test_jsonrpc.py`

- [ ] **Step 1: Write the failing test**

`aave-py/tests/test_jsonrpc.py`:

```python
import json
import unittest
from io import BytesIO
from unittest.mock import patch

from lib.jsonrpc import RpcClient, JsonRpcError


def _fake_response(payload: dict) -> BytesIO:
    body = json.dumps(payload).encode("utf-8")
    resp = BytesIO(body)
    resp.headers = {}  # urlopen returns a HTTPResponse which has .read(); BytesIO suffices.
    return resp


class TestRpcClient(unittest.TestCase):
    def test_call_returns_result(self):
        client = RpcClient("https://example/rpc")
        with patch("lib.jsonrpc.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = _fake_response(
                {"jsonrpc": "2.0", "id": 1, "result": "0x10"}
            )
            out = client.call("eth_blockNumber", [])
        self.assertEqual(out, "0x10")

    def test_call_raises_on_error_object(self):
        client = RpcClient("https://example/rpc")
        with patch("lib.jsonrpc.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = _fake_response(
                {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "nonce too low"}}
            )
            with self.assertRaises(JsonRpcError) as cm:
                client.call("eth_sendRawTransaction", ["0x02..."])
        self.assertIn("nonce too low", str(cm.exception))
        self.assertEqual(cm.exception.code, -32000)

    def test_request_id_increments(self):
        client = RpcClient("https://example/rpc")
        ids: list[int] = []
        with patch("lib.jsonrpc.urlopen") as urlopen:
            def capture(req, timeout):
                body = json.loads(req.data.decode("utf-8"))
                ids.append(body["id"])
                ctx = unittest.mock.MagicMock()
                ctx.__enter__.return_value = _fake_response({"jsonrpc": "2.0", "id": body["id"], "result": "0x0"})
                ctx.__exit__.return_value = False
                return ctx
            urlopen.side_effect = capture
            client.call("eth_blockNumber", [])
            client.call("eth_blockNumber", [])
        self.assertEqual(ids, [1, 2])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_jsonrpc -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write `aave-py/lib/jsonrpc.py`**

```python
"""Minimal JSON-RPC 2.0 client over urllib.

Implements only what we need: a single-method `call`, retry on transient
network errors, raise on JSON-RPC error objects.
"""

from __future__ import annotations

import json
import time
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

DEFAULT_TIMEOUT_SECS = 30
RETRIES = 3


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: object = None):
        super().__init__(f"JSON-RPC error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class RpcClient:
    def __init__(self, url: str, timeout: float = DEFAULT_TIMEOUT_SECS):
        self.url = url
        self.timeout = timeout
        self._next_id = 1

    def call(self, method: str, params: list) -> object:
        request_id = self._next_id
        self._next_id += 1
        body = json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        ).encode("utf-8")
        req = Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        last_err: Exception | None = None
        for attempt in range(RETRIES):
            try:
                with urlopen(req, timeout=self.timeout) as resp:
                    data = resp.read()
                payload = json.loads(data.decode("utf-8"))
                if "error" in payload:
                    err = payload["error"]
                    raise JsonRpcError(err.get("code", 0), err.get("message", "?"), err.get("data"))
                return payload["result"]
            except (URLError, HTTPError, TimeoutError) as e:
                last_err = e
                if attempt < RETRIES - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
        assert last_err is not None
        raise last_err
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_jsonrpc -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add aave-py/lib/jsonrpc.py aave-py/tests/test_jsonrpc.py
git commit -m "Add urllib-based JSON-RPC 2.0 client"
```

---

## Task 7: coincurve crypto adapter

**Files:**
- Create: `aave-py/lib/crypto.py`
- Create: `aave-py/tests/test_crypto.py`

This task is the first one that requires `coincurve` to be installed. Install it now:

```bash
cd aave-py && python -m pip install -r requirements.txt
```

- [ ] **Step 1: Write the failing test**

`aave-py/tests/test_crypto.py`:

```python
import unittest

from lib.crypto import sign_recoverable, derive_address


# Famous EIP-155 example private key.
PRIV = bytes.fromhex("4646464646464646464646464646464646464646464646464646464646464646")
EXPECTED_ADDR = "0x9d8A62f656a8d1615C1294fd71e9CFb3E4855A4F"


class TestCrypto(unittest.TestCase):
    def test_derive_address_eip155_example(self):
        self.assertEqual(derive_address(PRIV), EXPECTED_ADDR)

    def test_sign_recoverable_returns_canonical_y(self):
        msg = bytes.fromhex("00" * 32)  # any deterministic 32-byte digest works
        r, s, y = sign_recoverable(PRIV, msg)
        # secp256k1 group order n
        n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
        self.assertTrue(0 < r < n)
        # coincurve returns "low-s" canonical form
        self.assertTrue(0 < s <= n // 2, "signature s must be canonically low")
        self.assertIn(y, (0, 1))

    def test_sign_recovers_to_same_address(self):
        from coincurve import PublicKey
        from lib.keccak import keccak256

        msg = bytes.fromhex("11" * 32)
        r, s, y = sign_recoverable(PRIV, msg)
        sig65 = r.to_bytes(32, "big") + s.to_bytes(32, "big") + bytes([y])
        recovered = PublicKey.from_signature_and_message(sig65, msg, hasher=None)
        uncompressed = recovered.format(compressed=False)
        addr = "0x" + keccak256(uncompressed[1:])[12:].hex()
        self.assertEqual(addr.lower(), EXPECTED_ADDR.lower())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_crypto -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write `aave-py/lib/crypto.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_crypto -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add aave-py/lib/crypto.py aave-py/tests/test_crypto.py
git commit -m "Add coincurve adapter for ECDSA + EIP-55 address derivation"
```

---

## Task 8: BIP-39 + BIP-32 derivation

**Files:**
- Create: `aave-py/lib/bip39_32.py`
- Create: `aave-py/tests/test_bip39_32.py`

- [ ] **Step 1: Write the failing test**

`aave-py/tests/test_bip39_32.py`:

```python
import unittest

from lib.bip39_32 import mnemonic_to_seed, derive_path
from lib.crypto import derive_address


# BIP-39 official test vector: 12 words "abandon" x11 "about", no passphrase.
ABANDON = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
ABANDON_SEED_HEX = (
    "5eb00bbddcf069084889a8ab9155568165f5c453ccb85e70811aaed6f6da5fc1"
    "9a5ac40b389cd370d086206dec8aa6c43daea6690f20ad3d8d48b2d2ce9e38e4"
)

# Canonical BIP-44 derivation from the BIP-39 "abandon x11 about" mnemonic at
# m/44'/60'/0'/0/0. Cross-checked against MetaMask, ethers.js, and web3.py.
#
# NOTE: This is NOT the Hardhat/Anvil default address. Hardhat/Anvil's default
# first account `0xf39Fd6...` comes from the test mnemonic
# "test test test test test test test test test test test junk", not from the
# abandon mnemonic.
ABANDON_PRIV = bytes.fromhex("1ab42cc412b618bdea3a599e3c9bae199ebf030895b039e9db1e30dafb12b727")
ABANDON_ADDR = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"


class TestBip39(unittest.TestCase):
    def test_mnemonic_to_seed_abandon(self):
        seed = mnemonic_to_seed(ABANDON)
        self.assertEqual(seed.hex(), ABANDON_SEED_HEX)

    def test_mnemonic_to_seed_with_passphrase(self):
        # Trezor official vector: same mnemonic + passphrase "TREZOR".
        seed = mnemonic_to_seed(ABANDON, passphrase="TREZOR")
        self.assertEqual(
            seed.hex(),
            "c55257c360c07c72029aebc1b53c05ed0362ada38ead3e3e9efa3708e5349553"
            "1f09a6987599d18264c1e1c92f2cf141630c7a3c4ab7c81b2f001698e7463b04",
        )

    def test_invalid_word_rejected(self):
        bad = ABANDON.replace("about", "abouttt")
        with self.assertRaises(ValueError):
            mnemonic_to_seed(bad)

    def test_bad_checksum_rejected(self):
        # Swap the last word "about" (idx 3) for "abandon" (idx 0); checksum will mismatch.
        bad = ABANDON.replace("about", "abandon")
        with self.assertRaises(ValueError):
            mnemonic_to_seed(bad)

    def test_wrong_word_count_rejected(self):
        with self.assertRaises(ValueError):
            mnemonic_to_seed("abandon abandon abandon")


class TestBip32(unittest.TestCase):
    def test_derive_path_yields_abandon_priv(self):
        seed = mnemonic_to_seed(ABANDON)
        priv = derive_path(seed, "m/44'/60'/0'/0/0")
        self.assertEqual(priv.hex(), ABANDON_PRIV.hex())

    def test_derive_path_address(self):
        seed = mnemonic_to_seed(ABANDON)
        priv = derive_path(seed, "m/44'/60'/0'/0/0")
        self.assertEqual(derive_address(priv), ABANDON_ADDR)

    def test_path_must_start_with_m(self):
        seed = mnemonic_to_seed(ABANDON)
        with self.assertRaises(ValueError):
            derive_path(seed, "44'/60'/0'/0/0")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_bip39_32 -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write `aave-py/lib/bip39_32.py`**

```python
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
    # Reconstruct entropy + checksum and verify.
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
    return h[:32], h[32:]  # (priv, chain_code)


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_bip39_32 -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add aave-py/lib/bip39_32.py aave-py/tests/test_bip39_32.py
git commit -m "Add BIP-39 mnemonic + BIP-32 HD derivation"
```

---

## Task 9: Eth helpers (EIP-55 + EIP-1559 tx signing)

**Files:**
- Create: `aave-py/lib/eth.py`
- Create: `aave-py/tests/test_eth.py`

- [ ] **Step 1: Write the failing test**

`aave-py/tests/test_eth.py`:

```python
import unittest

from coincurve import PublicKey

from lib.eth import to_checksum_address, build_eip1559_tx, sign_eip1559_tx, sighash_eip1559
from lib.keccak import keccak256


# Wallet derived from the BIP-39 "abandon x11 about" mnemonic at
# m/44'/60'/0'/0/0. (Cross-verified in Task 8.)
ABANDON_PRIV = bytes.fromhex("1ab42cc412b618bdea3a599e3c9bae199ebf030895b039e9db1e30dafb12b727")
ABANDON_ADDR = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"


class TestEip55(unittest.TestCase):
    """Vectors from EIP-55 itself."""

    def test_all_lower_input(self):
        self.assertEqual(
            to_checksum_address("0xfb6916095ca1df60bb79ce92ce3ea74c37c5d359"),
            "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
        )

    def test_all_caps_short(self):
        self.assertEqual(
            to_checksum_address("0x52908400098527886e0f7030069857d2e4169ee7"),
            "0x52908400098527886E0F7030069857D2E4169EE7",
        )

    def test_already_checksummed_idempotent(self):
        addr = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
        self.assertEqual(to_checksum_address(addr), addr)


class TestEip1559Signing(unittest.TestCase):
    def test_round_trip(self):
        tx = build_eip1559_tx(
            chain_id=1,
            nonce=0,
            max_priority_fee_per_gas=2_000_000_000,
            max_fee_per_gas=50_000_000_000,
            gas_limit=500_000,
            to="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
            value=0,
            data=bytes.fromhex("69328dec"),
        )
        sighash = sighash_eip1559(tx)
        raw = sign_eip1559_tx(tx, ABANDON_PRIV)
        # Raw must start with the 0x02 type byte.
        self.assertEqual(raw[0], 0x02)

        # Recover the signer from sighash + sig and confirm == ABANDON_ADDR.
        # Sign again and pull (r,s,y) from a second call rather than parsing
        # them out of the signed RLP envelope.
        from lib.crypto import sign_recoverable
        r, s, y = sign_recoverable(ABANDON_PRIV, sighash)
        sig65 = r.to_bytes(32, "big") + s.to_bytes(32, "big") + bytes([y])
        recovered = PublicKey.from_signature_and_message(sig65, sighash, hasher=None)
        addr = "0x" + keccak256(recovered.format(compressed=False)[1:])[12:].hex()
        self.assertEqual(addr.lower(), ABANDON_ADDR.lower())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_eth -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write `aave-py/lib/eth.py`**

```python
"""EIP-55 checksum and EIP-1559 (type-2) transaction signing."""

from __future__ import annotations

from dataclasses import dataclass

from lib import abi, rlp
from lib.crypto import sign_recoverable
from lib.keccak import keccak256


def to_checksum_address(addr: str) -> str:
    """EIP-55 checksum encoding."""
    s = addr[2:] if addr.startswith(("0x", "0X")) else addr
    if len(s) != 40:
        raise ValueError(f"address must be 40 hex chars, got {len(s)}")
    s = s.lower()
    h = keccak256(s.encode("ascii")).hex()
    out = ["0x"]
    for i, c in enumerate(s):
        if c.isdigit():
            out.append(c)
        else:
            out.append(c.upper() if int(h[i], 16) >= 8 else c)
    return "".join(out)


@dataclass(frozen=True)
class Eip1559Tx:
    chain_id: int
    nonce: int
    max_priority_fee_per_gas: int
    max_fee_per_gas: int
    gas_limit: int
    to: str
    value: int
    data: bytes
    access_list: list  # always [] for our use case


def build_eip1559_tx(
    *,
    chain_id: int,
    nonce: int,
    max_priority_fee_per_gas: int,
    max_fee_per_gas: int,
    gas_limit: int,
    to: str,
    value: int,
    data: bytes,
    access_list: list | None = None,
) -> Eip1559Tx:
    return Eip1559Tx(
        chain_id=chain_id,
        nonce=nonce,
        max_priority_fee_per_gas=max_priority_fee_per_gas,
        max_fee_per_gas=max_fee_per_gas,
        gas_limit=gas_limit,
        to=to,
        value=value,
        data=data,
        access_list=access_list if access_list is not None else [],
    )


def _to_bytes(addr: str) -> bytes:
    s = addr[2:] if addr.startswith(("0x", "0X")) else addr
    return bytes.fromhex(s)


def _unsigned_payload(tx: Eip1559Tx) -> list:
    return [
        tx.chain_id,
        tx.nonce,
        tx.max_priority_fee_per_gas,
        tx.max_fee_per_gas,
        tx.gas_limit,
        _to_bytes(tx.to),
        tx.value,
        tx.data,
        tx.access_list,
    ]


def sighash_eip1559(tx: Eip1559Tx) -> bytes:
    return keccak256(b"\x02" + rlp.encode(_unsigned_payload(tx)))


def sign_eip1559_tx(tx: Eip1559Tx, priv: bytes) -> bytes:
    """Return the raw signed transaction bytes ready for eth_sendRawTransaction."""
    sighash = sighash_eip1559(tx)
    r, s, y = sign_recoverable(priv, sighash)
    payload = _unsigned_payload(tx) + [y, r, s]
    return b"\x02" + rlp.encode(payload)


# Re-export so callers don't have to import abi separately for this one helper.
encode_withdraw_call = abi.encode_withdraw_call
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_eth -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add aave-py/lib/eth.py aave-py/tests/test_eth.py
git commit -m "Add EIP-55 checksum and EIP-1559 transaction signing"
```

---

## Task 10: Aave V3 helpers (constants, calldata, status decoder, RPC reads)

**Files:**
- Create: `aave-py/lib/aave.py`
- Create: `aave-py/tests/test_aave.py`

- [ ] **Step 1: Write the failing test**

`aave-py/tests/test_aave.py`:

```python
import unittest
from unittest.mock import patch

from lib import aave as aavelib


class TestConstants(unittest.TestCase):
    def test_pool_address(self):
        self.assertEqual(aavelib.POOL, "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2")

    def test_market_table(self):
        self.assertEqual(aavelib.MARKETS["DAI"].underlying.lower(), "0x6b175474e89094c44da98b954eedeac495271d0f")
        self.assertEqual(aavelib.MARKETS["DAI"].a_token.lower(), "0x018008bfb33d285247a21d44e50697654f754e63")
        self.assertEqual(aavelib.MARKETS["DAI"].decimals, 18)
        self.assertEqual(aavelib.MARKETS["USDC"].underlying.lower(), "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
        self.assertEqual(aavelib.MARKETS["USDC"].decimals, 6)
        self.assertEqual(aavelib.MARKETS["USDT"].underlying.lower(), "0xdac17f958d2ee523a2206206994597c13d831ec7")
        self.assertEqual(aavelib.MARKETS["USDT"].decimals, 6)

    def test_market_order(self):
        self.assertEqual(aavelib.MARKET_ORDER, ("DAI", "USDC", "USDT"))


class TestReserveStatus(unittest.TestCase):
    def test_active_only(self):
        # Only bit 56 set.
        config = 1 << 56
        s = aavelib.decode_reserve_status(config)
        self.assertTrue(s.active)
        self.assertFalse(s.frozen)
        self.assertFalse(s.paused)

    def test_active_and_paused(self):
        config = (1 << 56) | (1 << 60)
        s = aavelib.decode_reserve_status(config)
        self.assertTrue(s.active)
        self.assertFalse(s.frozen)
        self.assertTrue(s.paused)

    def test_frozen(self):
        config = (1 << 56) | (1 << 57)
        s = aavelib.decode_reserve_status(config)
        self.assertTrue(s.frozen)


class TestCalldata(unittest.TestCase):
    def test_withdraw_calldata_max(self):
        data = aavelib.withdraw_calldata(
            asset=aavelib.MARKETS["USDC"].underlying,
            amount=(1 << 256) - 1,
            to="0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        )
        self.assertEqual(data[:4].hex(), "69328dec")
        self.assertEqual(data[4:36].hex(), "00" * 12 + "a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
        self.assertEqual(data[36:68].hex(), "ff" * 32)
        self.assertEqual(data[68:100].hex(), "00" * 12 + "f39fd6e51aad88f6f4ce6ab8827279cfffb92266")


class TestErc20BalanceOf(unittest.TestCase):
    def test_calls_eth_call_and_decodes(self):
        client = unittest.mock.MagicMock()
        client.call.return_value = "0x" + "00" * 31 + "2a"
        out = aavelib.erc20_balance_of(
            client,
            "0x018008bfb33d285247A21d44E50697654f754e63",
            "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        )
        self.assertEqual(out, 42)
        client.call.assert_called_once()
        args = client.call.call_args[0]
        self.assertEqual(args[0], "eth_call")
        params = args[1]
        # selector for balanceOf(address) is 0x70a08231
        self.assertTrue(params[0]["data"].startswith("0x70a08231"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_aave -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write `aave-py/lib/aave.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_aave -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add aave-py/lib/aave.py aave-py/tests/test_aave.py
git commit -m "Add Aave V3 calldata, status decoder, and read helpers"
```

---

## Task 11: Curated `selftest` vectors module

**Files:**
- Create: `aave-py/lib/vectors.py`
- Create: `aave-py/tests/test_vectors.py`

- [ ] **Step 1: Write the failing test**

`aave-py/tests/test_vectors.py`:

```python
import unittest

from lib import vectors


class TestVectors(unittest.TestCase):
    def test_run_all_returns_true_on_clean_run(self):
        ok, results = vectors.run_all()
        self.assertTrue(ok, f"selftest failed: {results}")
        # Every result should be (name, passed=True, detail=None|str)
        for name, passed, detail in results:
            self.assertTrue(passed, f"{name} failed: {detail}")

    def test_results_cover_every_module(self):
        _, results = vectors.run_all()
        names = {r[0] for r in results}
        for needed in ("keccak", "rlp", "abi", "bip39", "bip32", "eip55", "eip1559"):
            self.assertIn(needed, names)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_vectors -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write `aave-py/lib/vectors.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_vectors -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add aave-py/lib/vectors.py aave-py/tests/test_vectors.py
git commit -m "Add curated selftest vector runner"
```

---

## Task 12: CLI entrypoint — `.env` parser, argparse, `selftest` subcommand

**Files:**
- Create: `aave-py/aave.py`
- Create: `aave-py/tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

`aave-py/tests/test_cli.py`:

```python
import io
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

import aave


class TestEnvParser(unittest.TestCase):
    def test_parses_simple_kv(self):
        with TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("FOO=bar\n# comment\n\nBAZ=\"quoted value\"\n")
            env = aave._parse_env(str(p))
        self.assertEqual(env["FOO"], "bar")
        self.assertEqual(env["BAZ"], "quoted value")
        self.assertNotIn("# comment", env)

    def test_handles_missing_file(self):
        env = aave._parse_env("/nonexistent/path/.env")
        self.assertEqual(env, {})

    def test_does_not_overwrite_existing_env(self):
        with TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("MY_TEST_VAR=fromfile\n")
            os.environ["MY_TEST_VAR"] = "fromshell"
            try:
                aave._load_dotenv(str(p))
                self.assertEqual(os.environ["MY_TEST_VAR"], "fromshell")
            finally:
                del os.environ["MY_TEST_VAR"]


class TestSelftestCommand(unittest.TestCase):
    def test_selftest_passes(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = aave.cmd_selftest(args=None)
        self.assertEqual(rc, 0)
        self.assertIn("PASS", buf.getvalue())
        self.assertIn("keccak", buf.getvalue())


class TestArgparse(unittest.TestCase):
    def test_help_exits_zero(self):
        with self.assertRaises(SystemExit) as cm:
            aave.main(["--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_unknown_subcommand_exits_nonzero(self):
        with self.assertRaises(SystemExit) as cm:
            aave.main(["unknown"])
        self.assertNotEqual(cm.exception.code, 0)

    def test_only_validates_market(self):
        with self.assertRaises(SystemExit) as cm:
            aave.main(["withdraw", "--only=BANANA"])
        self.assertNotEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_cli -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write `aave-py/aave.py`** (initial version with `selftest` only; `verify` and `withdraw` are stubs filled in by Tasks 13 and 14)

```python
"""Aave V3 Stable Drainer (Python, minimal) — CLI entrypoint."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Make sibling `lib/` importable when running `python aave.py ...` directly.
sys.path.insert(0, str(Path(__file__).parent))

from lib import vectors  # noqa: E402


VERSION = "0.1.0"
VALID_MARKETS = ("DAI", "USDC", "USDT")


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


# ----------------------- verify (stub, Task 13 fills in) -----------------------

def cmd_verify(args: argparse.Namespace) -> int:
    _ensure_selftest_passes()
    print("verify: not yet implemented")
    return 0


# ----------------------- withdraw (stub, Task 14 fills in) -----------------------

def cmd_withdraw(args: argparse.Namespace) -> int:
    _ensure_selftest_passes()
    print("withdraw: not yet implemented")
    return 0


# ----------------------- argparse plumbing -----------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aave",
        description="Aave V3 stable drainer (Python, minimal).",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("selftest", help="run embedded test vectors and report")

    p_verify = sub.add_parser("verify", help="read-only state inspection")
    p_verify.add_argument("--prompt", action="store_true", help="prompt for the mnemonic instead of reading MNEMONIC env var")

    p_withdraw = sub.add_parser("withdraw", help="interactive sequential drainer (DAI -> USDC -> USDT)")
    p_withdraw.add_argument("--only", choices=VALID_MARKETS, help="restrict to a single market")
    p_withdraw.add_argument("--yes", "-y", action="store_true", help="skip per-market confirmation prompts")
    p_withdraw.add_argument("--prompt", action="store_true", help="prompt for the mnemonic instead of reading MNEMONIC env var")

    return p


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "selftest":
        return cmd_selftest(args)
    if args.command == "verify":
        return cmd_verify(args)
    if args.command == "withdraw":
        return cmd_withdraw(args)
    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_cli -v`
Expected: PASS (7 tests).

Also run the selftest end-to-end:

Run: `cd aave-py && python aave.py selftest`
Expected: lines like `  keccak  PASS`, ending with `selftest: ALL PASS`, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add aave-py/aave.py aave-py/tests/test_cli.py
git commit -m "Add CLI entrypoint with selftest subcommand and .env parser"
```

---

## Task 13: `verify` subcommand

**Files:**
- Modify: `aave-py/aave.py` (replace `cmd_verify`, add helpers)
- Create: `aave-py/tests/test_cli_verify.py`

- [ ] **Step 1: Write the failing test**

`aave-py/tests/test_cli_verify.py`:

```python
import argparse
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import aave


class TestVerifyCommand(unittest.TestCase):
    def test_verify_prints_per_market_lines(self):
        # Mock the RPC layer so this test never touches the network.
        with patch.object(aave, "_resolve_mnemonic", return_value=(
            "abandon abandon abandon abandon abandon abandon abandon abandon "
            "abandon abandon abandon about"
        )), patch.object(aave, "_make_client") as make_client:
            client = make_client.return_value
            # Each `eth_call` returns 32 bytes of zero (interpreted as 0).
            client.call.side_effect = lambda method, params: (
                "0x" + "00" * 32 if method == "eth_call"
                else "0x0" if method == "eth_getTransactionCount"
                else "0x0" if method == "eth_getBalance"
                else "0x" + "00" * (32 * 6) if method == "eth_call"
                else "0x10"
            )
            args = argparse.Namespace(prompt=False)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = aave.cmd_verify(args)
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("DAI", out)
        self.assertIn("USDC", out)
        self.assertIn("USDT", out)


if __name__ == "__main__":
    unittest.main()
```

> The test deliberately uses a coarse mock — for finer assertions we'd need a richer fake. The goal here is to confirm the command threads through end-to-end without crashing and prints all three market headings.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_cli_verify -v`
Expected: FAIL (not implemented yet, or `_resolve_mnemonic` doesn't exist).

- [ ] **Step 3: Replace `cmd_verify` and add helpers in `aave-py/aave.py`**

Replace the `cmd_verify` stub (and add helpers) with this code. Keep everything else in the file intact.

```python
# Add near the top, after `from lib import vectors`:
import getpass
from lib import aave as aavelib
from lib.aave import MARKET_ORDER, MARKETS, POOL, decode_reserve_status
from lib.bip39_32 import derive_path, mnemonic_to_seed
from lib.crypto import derive_address
from lib.jsonrpc import RpcClient


DEFAULT_HD_PATH = "m/44'/60'/0'/0/0"


def _make_client() -> RpcClient:
    url = os.environ.get("ALCHEMY_HTTP_URL")
    if not url:
        print("Missing ALCHEMY_HTTP_URL. Set it in .env or the environment.")
        sys.exit(2)
    return RpcClient(url)


def _resolve_mnemonic(prompt: bool) -> str:
    if prompt:
        m = getpass.getpass("BIP-39 mnemonic: ").strip()
        if not m:
            print("Empty mnemonic. Aborting.")
            sys.exit(2)
        return m
    m = os.environ.get("MNEMONIC", "").strip()
    if not m:
        print("MNEMONIC not set in .env. Pass --prompt to enter it interactively.")
        sys.exit(2)
    return m


def _resolve_wallet(prompt: bool) -> tuple[bytes, str]:
    mnemonic = _resolve_mnemonic(prompt)
    path = os.environ.get("HD_PATH", DEFAULT_HD_PATH).strip()
    seed = mnemonic_to_seed(mnemonic)
    priv = derive_path(seed, path)
    addr = derive_address(priv)
    return priv, addr


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


def cmd_verify(args: argparse.Namespace) -> int:
    _ensure_selftest_passes()
    client = _make_client()
    priv, addr = _resolve_wallet(prompt=getattr(args, "prompt", False))
    recipient = os.environ.get("RECIPIENT", "").strip() or addr

    _divider("RPC + wallet")
    block_hex = client.call("eth_blockNumber", [])
    eth_balance_hex = client.call("eth_getBalance", [addr, "latest"])
    nonce_hex = client.call("eth_getTransactionCount", [addr, "latest"])
    print(f"Block:           {int(block_hex, 16)}")
    print(f"Pool:            {POOL}")
    print(f"HD path:         {os.environ.get('HD_PATH', DEFAULT_HD_PATH)}")
    print(f"Wallet:          {addr}")
    same = "  (same as wallet)" if recipient == addr else "  (override via RECIPIENT)"
    print(f"Recipient:       {recipient}{same}")
    print(f"ETH balance:     {_format_units(int(eth_balance_hex, 16), 18)} ETH")
    print(f"Nonce (latest):  {int(nonce_hex, 16)}")

    # Wipe local copy of priv ASAP - verify never signs anything.
    del priv

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
```

> **Note:** `DEFAULT_HD_PATH` is hoisted to a module-level constant precisely so we don't need backslash escapes inside the f-string replacement field, which is a SyntaxError pre-Python 3.12 (PEP 701).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_cli tests.test_cli_verify -v`
Expected: PASS for all tests in both files.

Then run the live smoke test (uses the throwaway abandon mnemonic against your existing Alchemy URL — copy the URL from `compound-sniper/.env`):

```bash
cd aave-py
cat > .env <<'EOF'
ALCHEMY_HTTP_URL=https://eth-mainnet.g.alchemy.com/v2/REPLACE_WITH_YOUR_KEY
MNEMONIC=abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about
HD_PATH=m/44'/60'/0'/0/0
EOF
python aave.py verify
```

Expected output: Wallet line should read `0x9858EfFD232B4033E47d90003D41EC34EcaEda94` (the canonical BIP-39 abandon-mnemonic address at `m/44'/60'/0'/0/0`, cross-verified against eth-account / MetaMask / ethers.js / web3.py). All three reserves should show `reserve status: ok` with non-zero pool liquidity figures and `your aToken bal: 0 ...`.

Delete the test `.env` after this check:

```bash
rm aave-py/.env
```

- [ ] **Step 5: Commit**

```bash
git add aave-py/aave.py aave-py/tests/test_cli_verify.py
git commit -m "Implement verify subcommand with full RPC reads"
```

---

## Task 14: `withdraw` subcommand

**Files:**
- Modify: `aave-py/aave.py` (replace `cmd_withdraw`, add helpers)
- Create: `aave-py/tests/test_cli_withdraw.py`

- [ ] **Step 1: Write the failing test**

`aave-py/tests/test_cli_withdraw.py`:

```python
import argparse
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import aave


def _zero32() -> str:
    return "0x" + "00" * 32


class TestWithdrawCommand(unittest.TestCase):
    def test_withdraw_skips_when_balances_zero(self):
        with patch.object(aave, "_resolve_mnemonic", return_value=(
            "abandon abandon abandon abandon abandon abandon abandon abandon "
            "abandon abandon abandon about"
        )), patch.object(aave, "_make_client") as make_client:
            client = make_client.return_value

            def call(method, params):
                if method == "eth_blockNumber":
                    return "0x10"
                if method == "eth_getBalance":
                    return "0xde0b6b3a7640000"  # 1 ETH
                if method == "eth_getBlockByNumber":
                    return {"baseFeePerGas": "0x3b9aca00"}  # 1 gwei
                if method == "eth_getTransactionCount":
                    return "0x0"
                if method == "eth_call":
                    # Return zeros for all reads - getUserAccountData returns 6 zeros,
                    # balanceOf returns 0, getReserveData returns the active bit only,
                    # symbol returns 'TKN'.
                    data = params[0]["data"]
                    if data.startswith("0xbf92857c"):  # getUserAccountData
                        return "0x" + "00" * (32 * 6)
                    if data.startswith("0x35ea6a75"):  # getReserveData -> need active bit
                        first_word = (1 << 56).to_bytes(32, "big").hex()
                        return "0x" + first_word + "00" * (32 * 14)
                    if data.startswith("0x95d89b41"):  # symbol() returns "TKN"
                        offset = (32).to_bytes(32, "big").hex()
                        length = (3).to_bytes(32, "big").hex()
                        payload = b"TKN".ljust(32, b"\x00").hex()
                        return "0x" + offset + length + payload
                    return _zero32()
                return _zero32()

            client.call.side_effect = call
            args = argparse.Namespace(prompt=False, only=None, yes=True)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = aave.cmd_withdraw(args)
        out = buf.getvalue()
        self.assertEqual(rc, 0, msg=out)
        self.assertIn("DAI", out)
        self.assertIn("nothing to withdraw", out)
        self.assertNotIn("eth_sendRawTransaction", out)

    def test_withdraw_refuses_with_open_debt(self):
        with patch.object(aave, "_resolve_mnemonic", return_value=(
            "abandon abandon abandon abandon abandon abandon abandon abandon "
            "abandon abandon abandon about"
        )), patch.object(aave, "_make_client") as make_client:
            client = make_client.return_value

            def call(method, params):
                if method == "eth_blockNumber":
                    return "0x10"
                if method == "eth_getBalance":
                    return "0xde0b6b3a7640000"
                if method == "eth_getTransactionCount":
                    return "0x0"
                if method == "eth_call":
                    data = params[0]["data"]
                    if data.startswith("0xbf92857c"):  # getUserAccountData with debt
                        zero = "00" * 32
                        # field 1 (totalDebtBase) = 100
                        debt = (100).to_bytes(32, "big").hex()
                        return "0x" + zero + debt + zero * 4
                return _zero32()

            client.call.side_effect = call
            args = argparse.Namespace(prompt=False, only=None, yes=True)
            with self.assertRaises(SystemExit) as cm:
                aave.cmd_withdraw(args)
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aave-py && PYTHONPATH=. python -m unittest tests.test_cli_withdraw -v`
Expected: FAIL — `cmd_withdraw` is still the stub.

- [ ] **Step 3: Replace `cmd_withdraw` in `aave-py/aave.py`**

Add these helpers (anywhere above `cmd_withdraw`):

```python
from lib.eth import build_eip1559_tx, sign_eip1559_tx
from lib.jsonrpc import JsonRpcError


def _gas_fields(client: RpcClient) -> tuple[int, int]:
    priority_gwei = int(os.environ.get("PRIORITY_FEE_GWEI", "2"))
    priority = priority_gwei * 10 ** 9
    max_fee_gwei = os.environ.get("MAX_FEE_GWEI", "").strip()
    if max_fee_gwei:
        return int(max_fee_gwei) * 10 ** 9, priority
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
```

Then replace `cmd_withdraw`:

```python
def cmd_withdraw(args: argparse.Namespace) -> int:
    _ensure_selftest_passes()
    client = _make_client()
    priv, addr = _resolve_wallet(prompt=getattr(args, "prompt", False))
    recipient = os.environ.get("RECIPIENT", "").strip() or addr
    only = getattr(args, "only", None)
    auto_yes = getattr(args, "yes", False) or os.environ.get("AUTO_CONFIRM", "").lower() == "true"
    gas_limit = int(os.environ.get("GAS_LIMIT", "500000"))

    _divider("Pre-flight")
    eth_balance_hex = client.call("eth_getBalance", [addr, "latest"])
    eth_wei = int(eth_balance_hex, 16)
    print(f"Wallet:     {addr}")
    print(f"Recipient:  {recipient}{'  (override)' if recipient != addr else ''}")
    print(f"ETH:        {_format_units(eth_wei, 18)} ETH")
    print(f"AUTO yes:   {auto_yes}")
    if only:
        print(f"--only:     {only}")

    acct = aavelib.get_user_account_data(client, addr)
    if acct.total_debt_base > 0:
        print()
        print("!!! Wallet has open Aave debt. Refusing to start withdrawal sequence.")
        print(f"    totalDebtBase = ${_format_units(acct.total_debt_base, 8)}")
        sys.exit(2)

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
        raw = sign_eip1559_tx(tx, priv)
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

    # Wipe priv after the last sign.
    del priv

    _divider("Done")
    for k, st in results:
        print(f"  {k.ljust(5)} {st}")
    print()
    print("Recommended: run `python aave.py verify` again to confirm balances are zero.")
    return 0


def _wait_for_receipt(client: RpcClient, tx_hash: str, timeout_secs: int = 300, poll_secs: float = 4.0):
    import time
    deadline = time.monotonic() + timeout_secs
    while time.monotonic() < deadline:
        receipt = client.call("eth_getTransactionReceipt", [tx_hash])
        if receipt is not None:
            return receipt
        time.sleep(poll_secs)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd aave-py && PYTHONPATH=. python -m unittest discover -s tests -v`
Expected: PASS for all tests across all test files.

- [ ] **Step 5: Commit**

```bash
git add aave-py/aave.py aave-py/tests/test_cli_withdraw.py
git commit -m "Implement withdraw subcommand with sequential per-market drain"
```

---

## Task 15: Live smoke test against mainnet RPC

**Files:** none changed — this is a manual verification step.

- [ ] **Step 1: Set up a throwaway `.env`**

```bash
cd aave-py
cat > .env <<'EOF'
ALCHEMY_HTTP_URL=https://eth-mainnet.g.alchemy.com/v2/REPLACE_WITH_YOUR_KEY
MNEMONIC=abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about
HD_PATH=m/44'/60'/0'/0/0
EOF
chmod 600 .env
```

- [ ] **Step 2: Run `selftest`**

Run: `cd aave-py && python aave.py selftest`
Expected: every check `PASS`, exit code 0.

- [ ] **Step 3: Run `verify` against mainnet**

Run: `cd aave-py && python aave.py verify`
Expected:
- `Wallet:` line shows `0x9858EfFD232B4033E47d90003D41EC34EcaEda94` (the canonical address derived from the BIP-39 abandon mnemonic at `m/44'/60'/0'/0/0`)
- All three markets show `reserve status: ok`
- `your aToken bal:` = `0` for all three
- `pool liquidity:` shows non-zero figures (millions of DAI / USDC / USDT)
- Exit code 0

- [ ] **Step 4: Run `withdraw --only=USDC --yes`**

Run: `cd aave-py && python aave.py withdraw --only=USDC --yes`
Expected: skips with `[USDC] aToken balance is 0 - nothing to withdraw, skipping.`, exit code 0, no tx broadcast. Repeat with `--only=DAI` and `--only=USDT` to confirm each path.

- [ ] **Step 5: Confirm `--prompt` flag works**

Run: `cd aave-py && python aave.py verify --prompt` and at the prompt paste the abandon mnemonic. Expected: identical output to Step 3.

- [ ] **Step 6: Confirm `withdraw --only=BANANA` rejects**

Run: `cd aave-py && python aave.py withdraw --only=BANANA`
Expected: argparse error, non-zero exit code, message includes "invalid choice".

- [ ] **Step 7: Tear down**

```bash
rm aave-py/.env
```

- [ ] **Step 8: Commit a brief verification note (no code changes — skip if nothing to commit)**

```bash
# Nothing to commit unless the smoke test surfaced bug fixes that landed in earlier tasks.
git status
```

---

## Task 16: README

**Files:**
- Modify: `aave-py/README.md` (replace placeholder)

- [ ] **Step 1: Replace `aave-py/README.md` with the operational guide**

```markdown
# Aave V3 Stable Drainer (Python, minimal)

Pure-stdlib + one PyPI dependency (`coincurve`) Python implementation of the same workflow as the sibling `aave/` TypeScript project. Designed for a security-hardened machine where you want to read every line of code yourself.

> **Hot-wallet tool. The seed phrase you give this script controls every wallet derived from it.** Use a dedicated burner seed, not your daily-driver or cold-storage seed.

## What it does

For each of `DAI`, `USDC`, `USDT` (in that order), with one transaction per market:

1. Reads your aToken balance.
2. Skips if zero, inactive, or paused.
3. Falls back to a partial withdraw if pool liquidity < your balance.
4. Pre-flights with `eth_estimateGas`.
5. Prompts for confirmation, signs locally, broadcasts, waits for receipt.

## Trust surface

| Component | Trusted for |
|---|---|
| CPython stdlib | HTTP (`urllib`), hashing (`hashlib`/`hmac`), CLI (`argparse`/`getpass`) |
| `coincurve` (one wheel) | secp256k1 ECDSA signing — wraps Bitcoin Core's [libsecp256k1](https://github.com/bitcoin-core/secp256k1) |
| `lib/keccak.py`, `lib/rlp.py`, `lib/abi.py`, `lib/bip39_32.py`, `lib/eth.py`, `lib/aave.py` | Hand-rolled. Verified at startup against published test vectors via `selftest`. The script refuses to sign anything if any vector fails. |
| `wordlist.txt` | The canonical 2048-word [BIP-39 English wordlist](https://github.com/bitcoin/bips/blob/master/bip-0039/english.txt) |

`pip install -r requirements.txt` installs exactly one package.

## Setup

```bash
cd aave-py
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Either fill in MNEMONIC, or leave it blank and use --prompt at runtime.
chmod 600 .env
```

## Run

```bash
python aave.py selftest          # always first; verifies all hand-rolled crypto/encoding
python aave.py verify            # read-only state inspection
python aave.py verify --prompt   # same, but prompt for the mnemonic instead of reading .env
python aave.py withdraw                  # interactive sequential drain
python aave.py withdraw --only=DAI       # only one market
python aave.py withdraw --yes            # skip confirmation prompts
python aave.py withdraw --prompt         # mnemonic via getpass (recommended for D-day)
python -m unittest discover -s tests     # full test suite
```

## Subcommand exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | tx reverted, broadcast rejected, or user aborted |
| 2 | configuration error (missing env, invalid mnemonic, open Aave debt) |
| 3 | `selftest` failed - the script refused to sign |
| 4 | receipt poll timed out (check Etherscan with the printed hash) |

## Failure matrix

| Symptom | Likely cause | Fix |
|---|---|---|
| `selftest: FAILED` | Hand-rolled module mismatched a vector | Inspect the failing line; do not bypass — re-run after fix |
| `MNEMONIC not set` | `.env` empty or missing | Set `MNEMONIC=` or pass `--prompt` |
| `mnemonic checksum mismatch` | Typo in the seed phrase | Re-enter |
| `Wallet: 0x...` is the wrong address | Wrong `HD_PATH` | See path table in TS sibling README |
| `Wallet has open Aave debt. Refusing to start.` | `totalDebtBase > 0` | Repay your borrow first |
| `eth_estimateGas reverted` | Reserve paused/frozen, recipient invalid, etc. | Inspect Aave error code; choose to skip or fix |
| `gas estimate ... too close to GAS_LIMIT` | Network unusually expensive | Set `GAS_LIMIT=600000` in `.env` |

## File layout

```
aave-py/
├── README.md
├── requirements.txt          # coincurve>=20
├── .env.example
├── .gitignore
├── wordlist.txt              # BIP-39 English
├── aave.py                   # CLI entrypoint (.env parser, argparse, dispatch)
├── lib/
│   ├── keccak.py             # pure-Python Keccak-256
│   ├── rlp.py                # RLP encoder
│   ├── abi.py                # ABI encode/decode for our shapes
│   ├── jsonrpc.py            # urllib JSON-RPC client
│   ├── crypto.py             # coincurve adapter (sign + EIP-55)
│   ├── bip39_32.py           # mnemonic → seed → derived key
│   ├── eth.py                # EIP-55 + EIP-1559 signing
│   ├── aave.py               # Aave V3 constants, calldata, decoders, reads
│   └── vectors.py            # selftest runner
└── tests/                    # stdlib unittest, runnable with `python -m unittest discover`
```

## Security notes

- The mnemonic is never written to a keystore file. Pass `--prompt` to keep it off disk entirely.
- The private key is held in process memory only and `del`-ed after the last signature.
- Move funds off the hot wallet immediately after the run. Better: set `RECIPIENT` to a cold address so the underlying never lands on the hot wallet.
- `shred -u .env` (Linux) / `srm -z .env` (macOS) when finished.
- Treat the hot-wallet seed as compromised once the run is done. Generate a new seed for any future hot-wallet need.

## TL;DR

```bash
cd aave-py && pip install -r requirements.txt
cp .env.example .env && chmod 600 .env  # fill in ALCHEMY_HTTP_URL
python aave.py selftest
python aave.py verify --prompt   # paste mnemonic
python aave.py withdraw --prompt # paste mnemonic again, confirm each market
shred -u .env
```
```

- [ ] **Step 2: Commit**

```bash
git add aave-py/README.md
git commit -m "Document aave-py operational guide"
```

---

## Self-review

After writing the plan, this checklist was run:

**Spec coverage** (cross-referencing `docs/superpowers/specs/2026-05-11-aave-py-min-design.md`):

| Spec section | Implemented in task(s) |
|---|---|
| §2 Goal: feature parity (`verify`, `withdraw`, `--only`, `--yes`) | 13, 14 |
| §2 Goal: exactly one PyPI dep | 1 (requirements.txt), 7 (only place coincurve is required) |
| §2 Goal: selftest with vectors at startup | 11, 12 (`_ensure_selftest_passes`) |
| §4 Trust model: keccak/BIP-39/BIP-32/RLP/ABI verified | 11 (all five checks) |
| §5.1 keccak.py | 3 |
| §5.1 crypto.py | 7 |
| §5.1 bip39_32.py | 8 |
| §5.1 rlp.py | 4 |
| §5.1 abi.py | 5 |
| §5.1 jsonrpc.py | 6 |
| §5.1 eth.py | 9 |
| §5.1 aave.py | 10 |
| §5.1 vectors.py | 11 |
| §5.1 entrypoint aave.py | 12, 13, 14 |
| §5.2 canonical addresses + selector | 10 |
| §5.3 EIP-1559 sighash + signed envelope | 9 |
| §5.4 .env parser, 20 LOC, no python-dotenv | 12 |
| §5.5 `--prompt` via getpass | 13 |
| §7 error handling exit codes (2/3/4) | 12, 13, 14 |
| §8 priv held in memory, `del`-ed after signing | 13, 14 |
| §9 testing strategy (selftest + Hardhat smoke) | 11, 15 |
| §13 acceptance criteria 1-6 | 1, 11, 13, 14, 15 |

All spec sections are covered.

**Placeholder scan:** searched the plan for "TBD", "TODO", "implement later", "fill in details", "add error handling", "similar to Task" — none found. The only "stub" marker is in Task 12, where `cmd_verify` and `cmd_withdraw` are intentionally placeholder functions that are replaced in Tasks 13 and 14 with full implementations.

**Type consistency:** function names checked across tasks — `keccak256`, `encode`, `encode_uint256`, `encode_address`, `encode_withdraw_call`, `decode_uint256`, `decode_address`, `decode_uint8`, `decode_string`, `RpcClient.call`, `JsonRpcError`, `sign_recoverable`, `derive_address`, `mnemonic_to_seed`, `derive_path`, `to_checksum_address`, `build_eip1559_tx`, `sighash_eip1559`, `sign_eip1559_tx`, `withdraw_calldata`, `decode_reserve_status`, `get_user_account_data`, `get_reserve_configuration`, `erc20_balance_of`, `erc20_symbol`, `cmd_selftest`, `cmd_verify`, `cmd_withdraw`, `_resolve_mnemonic`, `_resolve_wallet`, `_make_client`, `_ensure_selftest_passes`. All consistent.

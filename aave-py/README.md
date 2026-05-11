# aave-py

Pure-stdlib + one PyPI dependency (`coincurve`) Python tool that withdraws supplied DAI / USDC / USDT positions from Aave V3 on Ethereum mainnet, using a hot wallet derived from a BIP-39 mnemonic. Same workflow as the sibling `aave/` TypeScript project, designed for a security-hardened machine where you want to read every line of code yourself.

> **Hot-wallet tool. The seed phrase you give this script controls every wallet derived from it.** Use a dedicated burner seed, not your daily-driver or cold-storage seed. Move funds off the hot wallet immediately when you're done.

## Table of contents

1. [What it does](#what-it-does)
2. [Trust surface](#trust-surface)
3. [Prerequisites](#prerequisites)
4. [Setup](#setup)
5. [Run](#run)
6. [`verify` example output](#verify-example-output)
7. [Subcommand exit codes](#subcommand-exit-codes)
8. [Failure matrix](#failure-matrix)
9. [Environment variables](#environment-variables)
10. [File layout](#file-layout)
11. [Testing](#testing)
12. [Security notes](#security-notes)
13. [TL;DR](#tldr)

---

## What it does

For each of `DAI`, `USDC`, `USDT` (in that order), with one transaction per market:

1. Reads your aToken balance (your supplied position including accrued interest).
2. Skips if zero, the reserve is `INACTIVE`, or the reserve is `PAUSED`.
3. Falls back to a partial withdraw if pool liquidity is less than your balance.
4. Pre-flights with `eth_estimateGas`.
5. Prompts for confirmation (or auto-confirms with `--yes`), signs locally with your in-memory key, broadcasts via `eth_sendRawTransaction`, and waits for the receipt.

A `selftest` subcommand runs published test vectors against every hand-rolled crypto/encoding module (Keccak-256, RLP, ABI, BIP-39, BIP-32, EIP-55, EIP-1559) at startup. **The script refuses to sign anything if any vector fails.**

---

## Trust surface

| Component | Trusted for | Why we trust it |
|---|---|---|
| CPython stdlib | HTTP (`urllib`), hashing (`hashlib`/`hmac`), CLI (`argparse`/`getpass`), JSON | Audited, ships with the OS Python |
| `coincurve` (one wheel) | secp256k1 ECDSA signing | Wraps Bitcoin Core's [libsecp256k1](https://github.com/bitcoin-core/secp256k1) — single-purpose, widely deployed |
| `lib/keccak.py` | Keccak-256 hashing | Verified at startup against Keccak KAT vectors (empty / "abc" / quick-brown-fox / 4 boundary lengths cross-checked vs. pycryptodome) |
| `lib/rlp.py` | RLP encoding | Verified against Ethereum yellow paper Appendix B vectors and list-length boundary tests |
| `lib/abi.py` | Solidity ABI encode/decode for our shapes | Pinned calldata fixtures + max-uint256 vector |
| `lib/bip39_32.py` | Mnemonic → seed → derived priv key | Verified against BIP-39 official "abandon × 11 about" vector AND BIP-32 reference test vector 1 |
| `lib/eth.py` | EIP-55 + EIP-1559 transaction signing | EIP-55 spec vectors + EIP-1559 round-trip recovery; the full signed envelope was cross-checked **byte-for-byte against `eth-account`** during development |
| `lib/aave.py` | Aave V3 calldata / decoders / RPC reads | Addresses verified char-for-char against [`bgd-labs/aave-address-book`](https://github.com/bgd-labs/aave-address-book); selectors recomputed via Keccak; reserve status bit decoder cross-checked with `ReserveConfiguration.sol` |
| `wordlist.txt` | The canonical 2048-word [BIP-39 English wordlist](https://github.com/bitcoin/bips/blob/master/bip-0039/english.txt) | SHA-256 enforced in test (`2f5eed53…3b24dbda`) — file corruption fails fast |

`pip install -r requirements.txt` installs **exactly one** package.

---

## Prerequisites

- **Python 3.13.** `coincurve` does not currently build under Python 3.14 (a known cffi/hatch packaging issue causes `RuntimeError: Expected exactly one LICENSE file in cffi distribution`). On macOS you can install 3.13 via `brew install python@3.13`. On Linux distros use your package manager or `pyenv`.
- An **Ethereum mainnet RPC URL**. Alchemy, Infura, your own node — anything that supports `eth_call`, `eth_getBalance`, `eth_getTransactionCount`, `eth_getBlockByNumber`, `eth_estimateGas`, `eth_sendRawTransaction`, `eth_getTransactionReceipt`.
- A **BIP-39 mnemonic** (12, 15, 18, 21, or 24 words) for the hot wallet that supplied to Aave. If your wallet uses a non-default derivation path, set `HD_PATH`.
- **Some ETH** in the hot wallet (~0.005 – 0.01 ETH per market is plenty at typical gas).

---

## Setup

```bash
cd aave-py

# Use Python 3.13 explicitly. coincurve doesn't build on 3.14 yet.
/opt/homebrew/bin/python3.13 -m venv .venv  # macOS / Homebrew
# or:  python3.13 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt  # installs exactly: coincurve

cp .env.example .env
# Either fill in MNEMONIC, or leave it blank and use `--prompt` at runtime.
chmod 600 .env
```

Confirm `.env` is gitignored:

```bash
git check-ignore -v .env
# expected: .gitignore:N:.env  .env
```

---

## Run

Always start with `selftest` to verify all the hand-rolled crypto matches its published test vectors:

```bash
python aave.py selftest
```

Expected:

```
  keccak   PASS
  rlp      PASS
  abi      PASS
  bip39    PASS
  bip32    PASS
  eip55    PASS
  eip1559  PASS

selftest: ALL PASS
```

Then read-only state inspection:

```bash
python aave.py verify              # reads MNEMONIC from .env
python aave.py verify --prompt     # prompts for mnemonic via getpass (recommended)
```

Then the live drain. `verify` and `withdraw` both run `selftest` internally before doing anything else.

```bash
python aave.py withdraw                  # interactive sequential drain DAI -> USDC -> USDT
python aave.py withdraw --only=DAI       # only one market
python aave.py withdraw --yes            # skip per-market confirmation prompts
python aave.py withdraw --prompt         # mnemonic via getpass; combine with --yes if desired
```

---

## `verify` example output

Run against the well-known BIP-39 abandon test mnemonic at `m/44'/60'/0'/0/0` (an empty test wallet):

```
========================================================================
RPC + wallet
========================================================================
Block:           25073986
Pool:            0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2
HD path:         m/44'/60'/0'/0/0
Wallet:          0x9858EfFD232B4033E47d90003D41EC34EcaEda94
Recipient:       0x9858EfFD232B4033E47d90003D41EC34EcaEda94  (same as wallet)
ETH balance:     0 ETH
Nonce (latest):  711

========================================================================
Aave account state
========================================================================
totalCollateralBase:  $0
totalDebtBase:        $0
availableBorrowsBase: $0
liquidationThreshold: 0%
ltv:                  0%
healthFactor:         INFINITE (no debt)

========================================================================
Per-market state
========================================================================

-- DAI (DAI)
   underlying:       0x6B175474E89094C44Da98b954EedeAC495271d0F
   aToken:           0x018008bfb33d285247A21d44E50697654f754e63
   reserve status:   ok
   your aToken bal:  0 DAI  (raw 0)
   pool liquidity:   18356827.218100467985667043 DAI

-- USDC (USDC)
   ...

========================================================================
Summary
========================================================================
Wallet:                0x9858EfFD232B4033E47d90003D41EC34EcaEda94
Markets with non-zero withdrawable balance: NONE.
```

The wallet `0x9858EfFD232B4033E47d90003D41EC34EcaEda94` is the canonical address derived from the BIP-39 abandon mnemonic at `m/44'/60'/0'/0/0` (cross-verified against eth-account, MetaMask, ethers.js, and web3.py). Note: this is **not** the Hardhat default — Hardhat's `0xf39Fd6...` address comes from the unrelated `"test test test test test test test test test test test junk"` test mnemonic.

---

## Subcommand exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | tx reverted, broadcast rejected, or user aborted |
| 2 | configuration error (missing env, invalid mnemonic, open Aave debt, missing `MNEMONIC` without `--prompt`) |
| 3 | `selftest` failed — the script refused to sign |
| 4 | receipt poll timed out (check Etherscan with the printed tx hash) |

---

## Failure matrix

| Symptom | Likely cause | Fix |
|---|---|---|
| `RuntimeError: Expected exactly one LICENSE file in cffi distribution` during `pip install` | You're on Python 3.14. coincurve doesn't currently build on it. | Use Python 3.13 (see Prerequisites) |
| `selftest: FAILED -- refusing to sign anything` | Hand-rolled module mismatched a vector | Inspect the failing line printed before "FAILED". Do not bypass; this is the load-bearing safety check. |
| `Missing ALCHEMY_HTTP_URL` | `.env` empty or missing | Fill in `ALCHEMY_HTTP_URL` |
| `MNEMONIC not set in .env` | `.env` empty for that key | Set `MNEMONIC=…` or pass `--prompt` |
| `mnemonic checksum mismatch` | Typo / wrong word / wrong order | Re-enter; cross-check the BIP-39 wordlist |
| `word not in BIP-39 wordlist: 'foo'` | Misspelled word | Re-enter |
| `Wallet:` prints the wrong address | Wrong `HD_PATH` | See path table in the TS sibling README; set `HD_PATH=m/44'/60'/i'/0/0` (Ledger Live) or `m/44'/60'/0'/0/i` (MetaMask/Rabby) where `i` is your account index |
| `Wallet has open Aave debt. Refusing to start.` | `totalDebtBase > 0` | Repay your borrow first; `withdraw` deliberately refuses to start with open debt to avoid `HEALTH_FACTOR_LOWER_THAN_LIQUIDATION_THRESHOLD` mid-sequence |
| `eth_estimateGas reverted: ...` | Reserve paused/frozen, recipient invalid, etc. | Read the underlying Aave error code; choose to skip and continue or abort |
| `gas estimate ... too close to GAS_LIMIT` | Network unusually expensive | Set `GAS_LIMIT=600000` in `.env` |
| `send rejected: nonce too low` | Pending tx with the same nonce | Wait for it to mine or set `MAX_FEE_GWEI` higher to replace it |
| `receipt timeout; check etherscan` | Block builders are slow / your fee was too low | Click the etherscan link; if it's pending, wait. Bump `PRIORITY_FEE_GWEI` and retry if needed. |

---

## Environment variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `ALCHEMY_HTTP_URL` | yes | — | Mainnet RPC. Despite the name, any provider works. |
| `MNEMONIC` | one of | — | 12/15/18/21/24-word BIP-39 phrase. Plaintext on disk. Or use `--prompt`. |
| `HD_PATH` | no | `m/44'/60'/0'/0/0` | Run `verify` to confirm the resulting address |
| `RECIPIENT` | no | wallet itself | If set, withdrawn underlying goes here directly (recommended: a cold wallet) |
| `GAS_LIMIT` | no | `500000` | Per-tx gas limit |
| `MAX_FEE_GWEI` | no | 2 × live baseFee + priority | Hard cap on `maxFeePerGas` |
| `PRIORITY_FEE_GWEI` | no | `2` | Tip to validator |
| `AUTO_CONFIRM` | no | `false` | If `true`, no prompts. `--yes` flag has the same effect. |

---

## File layout

```
aave-py/
├── README.md                  # this file
├── requirements.txt           # one line: coincurve>=20
├── .env.example
├── .gitignore                 # excludes .env, .venv/, venv/, __pycache__/, etc.
├── wordlist.txt               # canonical 2048-word BIP-39 English wordlist
├── aave.py                    # CLI entrypoint (argparse, .env parser, dispatch)
├── lib/
│   ├── __init__.py
│   ├── keccak.py              # pure-Python Keccak-256
│   ├── rlp.py                 # RLP encoder
│   ├── abi.py                 # ABI encode/decode for our exact shapes
│   ├── jsonrpc.py             # urllib-based JSON-RPC 2.0 client
│   ├── crypto.py              # coincurve adapter (sign + EIP-55)
│   ├── bip39_32.py            # mnemonic → seed → BIP-32 priv derivation
│   ├── eth.py                 # EIP-55 checksum + EIP-1559 tx assembly + signing
│   ├── aave.py                # Aave V3 constants, calldata, decoders, RPC reads
│   └── vectors.py             # selftest runner
└── tests/                     # stdlib unittest, runnable with `python -m unittest discover`
    ├── __init__.py
    ├── test_wordlist.py       # SHA-256 + length + spot-check word indices
    ├── test_keccak.py         # KAT vectors + boundary-length tests
    ├── test_rlp.py            # yellow paper vectors + list-length boundary
    ├── test_abi.py            # encode/decode shapes
    ├── test_jsonrpc.py        # mocked HTTP, retry behavior, 4xx vs 5xx
    ├── test_crypto.py         # EIP-155 example key, sign/recover round-trip
    ├── test_bip39_32.py       # abandon mnemonic, BIP-32 reference vector 1
    ├── test_eth.py            # EIP-55 vectors + EIP-1559 round-trip
    ├── test_aave.py           # constants, calldata, decoders, mock eth_call
    ├── test_vectors.py        # selftest runner is exhaustive
    ├── test_cli.py            # .env parser, argparse, selftest subcommand
    ├── test_cli_verify.py     # mocked verify end-to-end
    └── test_cli_withdraw.py   # mocked withdraw + debt-guard refusal
```

---

## Testing

```bash
PYTHONPATH=. python -m unittest discover -s tests
```

Should report `Ran 73 tests in <1s — OK`. The same vectors are also wired into the runtime `selftest` subcommand, so any divergence between dev and prod immediately fails the next run.

---

## Security notes

- **The mnemonic is held in process memory only.** Never written to a keystore file. After the last signature, the priv-key bytes are explicitly `del`-ed (best-effort given CPython's GC).
- **Pass `--prompt` on D-day.** It uses `getpass.getpass`, which means the seed never lands in `.env`, shell history, or process arglist.
- **`.env` should be `chmod 600`.** Confirm `git check-ignore -v .env` shows it's ignored before committing anything.
- **Use a dedicated hot-wallet seed.** Don't paste a seed that controls cold storage, your daily-driver, or anything else you'd cry over.
- **Move funds off the hot wallet immediately after the run.** Better: set `RECIPIENT` to a cold address so the underlying never lands on the hot wallet at all.
- **Securely delete `.env` when done.** On Linux: `shred -u .env`. On macOS: `srm -z .env` (if you have `brew install srm`) or just `rm -P .env`.
- **Treat the hot-wallet seed as compromised once the run is done.** Generate a fresh seed for any future hot-wallet need.

---

## TL;DR

```bash
cd aave-py
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && chmod 600 .env
$EDITOR .env                          # fill in ALCHEMY_HTTP_URL (leave MNEMONIC blank)

python aave.py selftest               # always first
python aave.py verify --prompt        # paste mnemonic; sanity-check the printed wallet address
python aave.py withdraw --prompt      # paste mnemonic again; confirm each market

shred -u .env                         # or srm -z .env on macOS
```

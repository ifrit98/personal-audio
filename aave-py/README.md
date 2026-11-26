# aave-py

Pure-stdlib + one PyPI dependency (`coincurve`) Python tool that withdraws supplied DAI / USDC / USDT positions from Aave V3 on Ethereum mainnet and consolidates them into a single stablecoin via Curve 3pool. Signs either with a key derived in-process from a BIP-39 mnemonic, or on a Ledger hardware wallet. Same workflow as the sibling `aave/` TypeScript project, designed for a security-hardened machine where you want to read every line of code yourself.

> **With `--signer mnemonic` this is a hot-wallet tool: the seed phrase you give it controls every wallet derived from it.** Use a dedicated burner seed, not your daily-driver or cold-storage seed, and move funds off when you're done. Use `--signer ledger` when the key must never touch the machine.

## Table of contents

1. [What it does](#what-it-does)
2. [Trust surface](#trust-surface)
3. [Prerequisites](#prerequisites)
4. [Setup](#setup)
5. [Run](#run) — including [finding the right wallet](#finding-the-right-wallet-first-aave-scan_aave), [signing backends](#choosing-a-signing-backend), and [consolidating](#consolidating-to-one-stablecoin-aave-swap)
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

**`withdraw`** — for each of `DAI`, `USDC`, `USDT` (in that order), with one transaction per market:

1. Reads your aToken balance (your supplied position including accrued interest).
2. Skips if zero, the reserve is `INACTIVE`, or the reserve is `PAUSED`.
3. Falls back to a partial withdraw if pool liquidity is less than your balance.
4. Pre-flights with `eth_estimateGas`.
5. Prompts for confirmation (or auto-confirms with `--yes`), signs, broadcasts via `eth_sendRawTransaction`, and waits for the receipt.

**`swap`** — consolidates leftover stablecoin balances into one asset (default DAI) through [Curve 3pool](https://etherscan.io/address/0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7). Curve was chosen over an aggregator because it needs **no off-chain quote API**: `get_dy` prices the trade on-chain, so the minimum-output guard comes from the same contract that executes the swap. Before trading it re-reads `coins(0..2)` from the live pool and refuses if the layout isn't `DAI=0, USDC=1, USDT=2`, then re-quotes immediately before each execution so a stale price can't be used.

**`scan_aave`** — read-only. Derives addresses from every `MNEMONIC*` env var across a range of HD paths and reports which ones actually hold aTokens. Use this to find the supplying wallet rather than guessing a derivation path. Never signs.

**`verify`** — read-only state inspection. Releases the key immediately after deriving the address.

A `selftest` subcommand runs published test vectors against every hand-rolled crypto/encoding module (Keccak-256, RLP, ABI, BIP-39, BIP-32, EIP-55, EIP-1559) at startup. **The script refuses to sign anything if any vector fails.**

### The `withdraw` + `swap` shortcut

Aave's `withdraw(asset, amount, to)` takes an arbitrary recipient. If your goal is "get everything to one address", set `RECIPIENT` and the underlying lands there directly — no separate sweep, no gas needed in intermediate wallets. Only use `swap` when you additionally need the balances converted to a single currency.

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
| `lib/curve.py` | Curve 3pool calldata + on-chain quoting | Selectors recomputed from signatures and pinned in tests against values read from the live contract; coin layout re-verified on-chain before every swap |
| `lib/signer.py` | Choosing and driving a signing backend | Thin dispatch only; the expected-address guard is covered by tests including the forgotten-passphrase case |
| `lib/ledger.py` | Ledger APDU / HID transport | Hand-rolled against the documented protocol. Path encoding, HID framing, chunking, and response parsing are unit-tested; signatures are recovered and checked against the device's own address before broadcast |
| `wordlist.txt` | The canonical 2048-word [BIP-39 English wordlist](https://github.com/bitcoin/bips/blob/master/bip-0039/english.txt) | SHA-256 enforced in test (`2f5eed53…3b24dbda`) — file corruption fails fast |

`pip install -r requirements.txt` installs **exactly one** package. Ledger support adds exactly one more (`hidapi`, a thin wrapper around the widely-deployed hidapi C library, shipped as a prebuilt wheel with no transitive dependencies) and is kept in a separate `requirements-ledger.txt` so you only take it on if you need it. This deliberately avoids the `@ledgerhq` / `ledgereth` stacks, which would pull in dozens of packages.

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

Two equivalent ways to invoke:

| | Direct | Via thin wrapper |
|---|---|---|
| Setup | `source .venv/bin/activate` first, then `python aave.py …` | works from anywhere; auto-resolves the venv `python3` and sets `PYTHONPATH=.` |
| Example | `python aave.py selftest` | `./aave selftest` |

The `./aave` wrapper is just a 30-line bash file that exec's `.venv/bin/python3 aave.py "$@"` — no behavior change, just less typing under stress.

Always start with `selftest` to verify all the hand-rolled crypto matches its published test vectors:

```bash
./aave selftest
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
./aave verify              # reads MNEMONIC from .env
./aave verify --prompt     # prompts for mnemonic via getpass (recommended)
```

Then the live drain. `verify`, `withdraw`, and `swap` all run `selftest` internally before doing anything else.

```bash
./aave withdraw                  # interactive sequential drain DAI -> USDC -> USDT
./aave withdraw --only=DAI       # only one market
./aave withdraw --yes            # skip per-market confirmation prompts
./aave withdraw --prompt         # mnemonic via getpass; combine with --yes if desired
./aave withdraw --allow-debt     # proceed despite real open debt (risks a HF revert)
```

### Finding the right wallet first (`./aave scan_aave`)

If you keep several seeds, or aren't certain which derivation path supplied to Aave, don't guess — scan. This is read-only and never signs.

```bash
./aave scan_aave                        # every MNEMONIC* var, 5 indices per path pattern
./aave scan_aave --depth 20             # widen the search
./aave scan_aave --only-var MNEMONIC_TREASURY
./aave scan_aave --json manifest.json   # machine-readable output
```

It covers MetaMask/Rabby (`m/44'/60'/0'/0/i`), Ledger Live (`m/44'/60'/i'/0/0`), and legacy MEW (`m/44'/60'/0'/i`) patterns, and prints the `--mnemonic-var` + `HD_PATH` pair to feed the other subcommands. Exit code is `0` if it found a position, `1` if not.

### Choosing a signing backend

```bash
./aave verify                                    # default: mnemonic from MNEMONIC
./aave verify --mnemonic-var MNEMONIC_TREASURY   # use a namespaced key, no copying
./aave verify --passphrase                       # prompt for the BIP-39 25th word
./aave verify --hd-path "m/44'/60'/0'/0/3"       # non-default account index
./aave withdraw --signer ledger                  # sign on the device instead
```

**Always pin the address you expect.** This is the cheapest guard against a wrong HD path, a forgotten passphrase, or the wrong mnemonic env var — each of which otherwise produces a perfectly valid signature from a wallet you didn't mean to use:

```bash
./aave withdraw --expect-address 0xYourWallet    # or set EXPECTED_ADDRESS in .env
```

#### If a correct seed derives the wrong address

Almost always one of three things, in order of likelihood:

1. **A BIP-39 passphrase.** Pass `--passphrase`. Omitting one silently derives a completely different, usually empty, wallet — it does not error.
2. **A non-default HD path.** Run `./aave scan_aave --depth 20` and let it find the path.
3. **A hardware wallet.** If no seed derives the address, there may not be one — try `--signer ledger`.

#### Using a Ledger

```bash
.venv/bin/pip install -r requirements-ledger.txt   # one extra package
./aave withdraw --signer ledger --hd-path "m/44'/60'/0'/0/0"
```

Unlock the device and open the Ethereum app first. Aave and Curve aren't in Ledger's clear-signing registry, so **"Blind signing" must be enabled** in the Ethereum app settings; the device will then show the function selector and raw calldata, which should match what `verify` printed. Close Ledger Live before running — it holds an exclusive claim on the USB interface. Every signature is recovered and checked against the device's own address before broadcast, so a wrong-derivation signature can't reach the mempool.

### Consolidating to one stablecoin (`./aave swap`)

```bash
./aave swap                              # everything -> DAI
./aave swap --to USDC                    # everything -> USDC
./aave swap --source USDT --to DAI       # just one leg
./aave swap --slippage-bps 25            # loosen the guard (default 10)
./aave swap --yes                        # skip confirmation
```

For each leg it reads your balance, quotes it with `get_dy`, sets `min_dy` to the quote minus your slippage tolerance, approves **exactly** the amount being swapped (never unlimited), and executes. It re-quotes immediately before sending so the guard is never computed from a stale price.

Costs are small but not zero — a 125,000 USDC → DAI swap prices out around **$19 (0.015%)**, essentially all of it the pool fee, since that size is a fraction of a percent of 3pool's reserves. **If your destination accepts the asset you already hold, skip this step**; swapping purely to change denomination is pure cost.

### Wallet inspector (`./aave wallet`)

A read-only utility that shows your wallet's ETH + ERC-20 balances and recent transfer history using Alchemy's `alchemy_getAssetTransfers` API:

```bash
./aave wallet                    # reads MNEMONIC from .env; shows balances + last 20 transfers
./aave wallet --prompt           # prompt for mnemonic
./aave wallet --last 50          # show last 50 transfers
./aave wallet --path "m/44'/60'/0'/0/1"  # inspect a different derivation index
```

Displays: ETH, WETH, DAI, USDC, USDT, and WBTC balances, plus a transfer history table with direction (IN/OUT), asset, value, counterparty, and timestamp.

### Pre-flight rehearsal (`./rehearse.sh`)

A separate `./rehearse.sh` runs the entire pipeline against the public BIP-39 abandon mnemonic (a well-known empty wallet on mainnet). It writes a temporary `.env`, runs `selftest` → `verify` → `withdraw --only={DAI,USDC,USDT} --yes` end-to-end against your real RPC, asserts the expected wallet derives, all reserves are healthy, every market skips cleanly with no broadcast, and that argparse rejects bad input. Restores your real `.env` afterward and never touches real funds.

Use it to:

- Catch environment regressions before D-day (Python version, venv state, `coincurve` install, RPC reachability).
- Smoke-test a freshly-cloned checkout in 10 seconds.
- Validate that a `.env` URL change (new Alchemy key, new provider) hasn't broken anything.

```bash
./rehearse.sh https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY
# or, with ALCHEMY_HTTP_URL already in your .env:
./rehearse.sh
```

Expected output:

```
=== aave-py rehearsal ===
RPC: https://...
Wallet (expected): 0x9858EfFD232B4033E47d90003D41EC34EcaEda94

[REHEARSE    ] selftest ... PASS
[REHEARSE    ] verify wallet derives correctly ... PASS
[REHEARSE    ] verify reports healthy DAI reserve ... PASS
[REHEARSE    ] verify reports zero balances ... PASS
[REHEARSE    ] withdraw --only=DAI --yes skips, no broadcast ... PASS
[REHEARSE    ] withdraw --only=USDC --yes skips, no broadcast ... PASS
[REHEARSE    ] withdraw --only=USDT --yes skips, no broadcast ... PASS
[REHEARSE    ] withdraw --only=BANANA rejected by argparse ... PASS

=== summary: 8 passed, 0 failed ===
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
| `mnemonic checksum mismatch` | Typo / wrong word / wrong order | Re-enter; cross-check the BIP-39 wordlist. Some wallets emit non-compliant phrases — `--skip-checksum` accepts those. |
| `word not in BIP-39 wordlist: 'foo'` | Misspelled word | Re-enter |
| `MNEMONIC is not set` | Seed is stored under a namespaced key | The error lists the `MNEMONIC*` vars it found; pass `--mnemonic-var NAME` |
| `derived address does not match the expected address` | Wrong path, missing passphrase, or wrong seed | Try `--passphrase`, then `./aave scan_aave --depth 20` to find the real path |
| `Wallet:` prints the wrong address | Wrong `HD_PATH`, or a BIP-39 passphrase you didn't supply | Run `./aave scan_aave --depth 20`. A missing passphrase silently derives a *different valid* wallet rather than erroring. |
| `Wallet has open Aave debt. Refusing to start.` | `totalDebtBase` above the dust floor | Repay your borrow first; `withdraw` refuses to start with real open debt to avoid `HEALTH_FACTOR_LOWER_THAN_LIQUIDATION_THRESHOLD` mid-sequence. Override with `--allow-debt`, or raise `DEBT_DUST_THRESHOLD_BASE`. |
| `no Ledger device found over USB` | Not plugged in, locked, or wrong app | Unlock and open the Ethereum app |
| `could not open the Ledger HID interface` | Ledger Live is holding the device | Quit Ledger Live and retry |
| `device error 0x6a80 ... enable 'Blind signing'` | Aave/Curve aren't clear-signable | Enable Blind signing in the Ethereum app settings |
| `device error 0x6985` | You pressed Reject, or it timed out | Retry and approve on the device |
| `Ledger support needs the 'hidapi' package` | Optional extra not installed | `.venv/bin/pip install -r requirements-ledger.txt` |
| `Curve 3pool coin layout mismatch` | Pool returned an unexpected coin | Do not override. Investigate before trading. |
| `eth_estimateGas reverted` on a swap | Approve hasn't settled, or `min_dy` too tight | Re-run (it re-quotes), or raise `--slippage-bps` |
| `eth_estimateGas reverted: ...` | Reserve paused/frozen, recipient invalid, etc. | Read the underlying Aave error code; choose to skip and continue or abort |
| `gas estimate ... too close to GAS_LIMIT` | Network unusually expensive | Set `GAS_LIMIT=600000` in `.env` |
| `send rejected: nonce too low` | Pending tx with the same nonce | Wait for it to mine or set `MAX_FEE_GWEI` higher to replace it |
| `receipt timeout; check etherscan` | Block builders are slow / your fee was too low | Click the etherscan link; if it's pending, wait. Bump `PRIORITY_FEE_GWEI` and retry if needed. |

---

## Environment variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `ALCHEMY_HTTP_URL` | yes | — | Mainnet RPC. Despite the name, any provider works. |
| `SIGNER` | no | `mnemonic` | `mnemonic` or `ledger`. `--signer` overrides. |
| `MNEMONIC` | one of | — | 12/15/18/21/24-word BIP-39 phrase. Plaintext on disk. Or use `--prompt`. |
| `MNEMONIC_VAR` | no | `MNEMONIC` | Read the seed from a differently-named env var instead of copying it |
| `BIP39_PASSPHRASE` | no | empty | The "25th word". Changes every derived address. Prefer `--passphrase`. |
| `HD_PATH` | no | `m/44'/60'/0'/0/0` | Run `verify` to confirm the resulting address |
| `LEDGER_PATH` | no | falls back to `HD_PATH` | Used only when `SIGNER=ledger` |
| `EXPECTED_ADDRESS` | no | — | Abort unless the signer resolves to this address. Strongly recommended. |
| `RECIPIENT` | no | wallet itself | If set, withdrawn underlying goes here directly (recommended: a cold wallet) |
| `GAS_LIMIT` | no | `500000` | Per-tx gas limit for withdrawals |
| `SWAP_GAS_LIMIT` | no | `400000` | Per-tx gas limit for Curve exchanges |
| `MAX_FEE_GWEI` | no | 2 × live baseFee + priority | Hard cap on `maxFeePerGas` |
| `PRIORITY_FEE_GWEI` | no | `2` | Tip to validator |
| `SLIPPAGE_BPS` | no | `10` | Max slippage vs the on-chain quote, in basis points |
| `DEBT_DUST_THRESHOLD_BASE` | no | `1000000` ($0.01) | Debt at or below this is treated as rounding dust |
| `AUTO_CONFIRM` | no | `false` | If `true`, no prompts. `--yes` flag has the same effect. |

---

## File layout

```
aave-py/
├── README.md                  # this file
├── requirements.txt           # one line: coincurve>=20
├── requirements-ledger.txt    # optional extra: hidapi (only for --signer ledger)
├── .env.example
├── .gitignore                 # excludes .env, .venv/, venv/, __pycache__/, etc.
├── wordlist.txt               # canonical 2048-word BIP-39 English wordlist
├── aave                       # thin shell wrapper: `./aave selftest|verify|withdraw|swap|scan_aave`
├── rehearse.sh                # automated mainnet rehearsal vs. abandon mnemonic
├── aave.py                    # CLI entrypoint (argparse, .env parser, dispatch)
├── scan_aave.py               # read-only: find which wallet holds the Aave position
├── lib/
│   ├── __init__.py
│   ├── keccak.py              # pure-Python Keccak-256
│   ├── rlp.py                 # RLP encoder
│   ├── abi.py                 # ABI encode/decode for our exact shapes
│   ├── jsonrpc.py             # urllib-based JSON-RPC 2.0 client
│   ├── crypto.py              # coincurve adapter (sign, recover, EIP-55)
│   ├── bip39_32.py            # mnemonic → seed → BIP-32 priv derivation
│   ├── eth.py                 # EIP-55 checksum + EIP-1559 tx assembly + signing
│   ├── aave.py                # Aave V3 constants, calldata, decoders, RPC reads
│   ├── curve.py               # Curve 3pool calldata + on-chain get_dy quoting
│   ├── signer.py              # signer backends behind one address/sign interface
│   ├── ledger.py              # hand-rolled Ledger APDU over USB HID
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

Should report `Ran 151 tests in <1s — OK`. The same vectors are also wired into the runtime `selftest` subcommand, so any divergence between dev and prod immediately fails the next run.

**One caveat on the Ledger code.** Every pure function in `lib/ledger.py` is unit-tested — path encoding, HID framing round-trips, APDU chunking, status-word handling, response parsing — and the transport is driven against a scripted fake device. But the protocol has **not yet been exercised against real hardware**. Before trusting it with value, plug in your device and confirm the address is what you expect:

```bash
./aave verify --signer ledger --expect-address 0xYourWallet
```

That reads the address over USB without signing anything, so it validates the transport end-to-end at zero risk.

---

## Security notes

- **Pin `EXPECTED_ADDRESS`.** The single highest-value guard in the tool. A wrong HD path or a forgotten BIP-39 passphrase does not error — it derives a different, valid, usually empty wallet and happily signs from it.
- **Prefer `--signer ledger` for anything you'd cry over.** The key never reaches the machine and every transaction needs a physical confirmation.
- **Keep one seed per `.env`.** If a `.env` accumulates many seeds it becomes a single catastrophic target, and `rehearse.sh` deletes and recreates `.env` as part of normal operation — which will take those seeds with it if interrupted. Run the rehearsal from a throwaway copy of the directory if your `.env` holds anything irreplaceable.
- **The mnemonic is held in process memory only.** Never written to a keystore file. After the last signature the key reference is dropped (best-effort given CPython's GC).
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
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env
$EDITOR .env                          # fill in ALCHEMY_HTTP_URL (leave MNEMONIC blank)

./rehearse.sh                         # ~20s sanity check against the abandon wallet
./aave selftest                       # always first
./aave scan_aave                      # read-only: which wallet/path holds the position?
./aave verify --prompt --expect-address 0xYourWallet
./aave withdraw --prompt --expect-address 0xYourWallet
./aave swap --to DAI                  # only if you need a single currency

shred -u .env                         # or srm -z .env on macOS
```

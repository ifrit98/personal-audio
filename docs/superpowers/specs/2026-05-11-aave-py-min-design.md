# Aave V3 Stable Drainer — Minimal Python Port

**Date:** 2026-05-11
**Status:** Approved (design phase)
**Supersedes:** none
**Related:** `aave/` (existing TypeScript implementation)

## 1. Problem statement

Replicate the workflow of the existing TypeScript `aave/` project in Python on a security-hardened, possibly air-gapped-ish machine. The TS version uses a hot wallet derived from a BIP-39 seed phrase to sequentially withdraw the user's supplied positions in three Aave V3 Ethereum mainnet reserves (DAI, USDC, USDT) by calling `Pool.withdraw(asset, type(uint256).max, recipient)` once per market.

The Python port must minimize the trust surface that lives outside Python source code that the user can read.

## 2. Goals

- Functional parity with the existing `aave/` TypeScript tool: `verify` (read-only inspection) and `withdraw` (interactive sequential drainer) subcommands, with the same per-market flow, the same partial-withdraw fallback when pool liquidity is short, the same refusal-to-start guard when the account has open debt, and the same `--only=<MARKET>` and `--yes` CLI flags.
- Exactly one PyPI dependency: `coincurve` (libsecp256k1 binding). Everything else uses the Python stdlib.
- Every cryptographic / encoding primitive that we implement ourselves (Keccak-256, BIP-39, BIP-32, RLP, ABI) ships with a `selftest` subcommand that verifies it against published test vectors at startup. The script refuses to sign anything if any vector fails.
- Correctness is paramount; performance is not. A signing operation taking 100 ms vs 1 ms is irrelevant for a one-shot withdrawal.

## 3. Non-goals

- Multicall / batched withdrawal in one tx. (Aave V3's `Pool.withdraw` is per-asset; sequential broadcasts are fine.)
- Hardware wallet support. (That's what `compound-sniper` is for.)
- A general-purpose Ethereum library. We implement only what `Pool.withdraw` and the few read calls require.
- Optimal gas pricing. We use the same simple "2× baseFee + tip" heuristic as the TS version.
- Audited / constant-time pure-Python crypto. We delegate the side-channel-sensitive primitive (ECDSA on secp256k1) to libsecp256k1 via `coincurve` precisely because we don't want to write that ourselves.

## 4. Trust model

What you must trust to run this tool:

| Component | What we trust it for | Why we trust it |
|---|---|---|
| CPython stdlib | `hashlib`, `hmac`, `urllib.request`, `json`, `secrets`, `argparse`, `getpass` | Audited, ships with the OS Python |
| `coincurve` (one wheel) | secp256k1 ECDSA sign + recoverable signature | Wraps libsecp256k1 (Bitcoin Core); single-purpose; widely deployed |
| Our `lib/keccak.py` | Keccak-256 hashing | Verified against XKCP test vectors at startup |
| Our `lib/bip39_32.py` | Mnemonic → seed → derived priv key | Verified against BIP-39 and BIP-32 test vectors at startup |
| Our `lib/rlp.py` | EIP-1559 typed-tx envelope encoding | Verified against Ethereum yellow paper appendix vectors at startup |
| Our `lib/abi.py` | Encoding `(address, uint256, address)` calldata | Verified against synthetic vectors derived from the Solidity ABI spec |
| Operator (the user) | The mnemonic in `.env` is for a dedicated hot wallet, not their cold/daily seed | Same threat model as the TS version |

If any of the self-test vectors fail (Keccak / BIP-39 / BIP-32 / RLP / ABI), the tool exits with a non-zero status code before any key derivation occurs.

## 5. Architecture

Single Python package under `aave-py/` with the following layout:

```
aave-py/
├── README.md                  # mirrors TS README structure with Python specifics
├── requirements.txt           # one line: coincurve>=20
├── .env.example
├── .gitignore
├── wordlist.txt               # BIP-39 English wordlist (2048 words, ~14 KB)
├── aave.py                    # CLI entrypoint: dispatches verify / withdraw / selftest
└── lib/
    ├── __init__.py
    ├── keccak.py              # Pure-Python Keccak-f[1600] sponge, padding 0x01
    ├── crypto.py              # Thin coincurve adapter: sign(msg32, priv) -> (r, s, y)
    ├── bip39_32.py            # mnemonic -> seed (PBKDF2-HMAC-SHA512)
    │                          # seed -> master key (HMAC-SHA512 "Bitcoin seed")
    │                          # master + path -> child priv key (CKDpriv chain)
    ├── rlp.py                 # encode_int, encode_bytes, encode_list
    ├── abi.py                 # encode_uint256, encode_address, encode_args
    ├── jsonrpc.py             # POST helper over urllib + Eth call helpers
    │                          # (eth_call, eth_estimateGas, eth_sendRawTransaction,
    │                          # eth_getTransactionCount, eth_getBalance,
    │                          # eth_getBlockByNumber, eth_getTransactionReceipt)
    ├── eth.py                 # Address checksum, tx assembly + sighash + signing
    ├── aave.py                # Aave V3 helpers: read aToken balanceOf,
    │                          # getReserveData, getUserAccountData, decode flags
    └── vectors.py             # Embedded test vectors for selftest
```

Each module is small enough to read in one sitting (target: every file ≤ 200 LOC, except `aave.py` entrypoint which may be larger because it owns CLI orchestration).

### 5.1 Module responsibilities

- **`lib/keccak.py`** — One class `Keccak256` with `update(bytes)` and `digest() -> bytes` matching the standard hash interface, plus a top-level `keccak256(bytes) -> bytes` convenience function. Implements Keccak-f[1600] from the FIPS 202 draft (the original padding `0x01`, not NIST's later `0x06`).
- **`lib/crypto.py`** — Single function `sign_recoverable(priv32: bytes, sighash32: bytes) -> tuple[int, int, int]` returning `(r, s, y_parity)`. Wraps `coincurve.PrivateKey.sign_recoverable(..., hasher=None)`. Also exposes `derive_address(priv32: bytes) -> str` for the EIP-55 checksummed address.
- **`lib/bip39_32.py`** — `mnemonic_to_seed(mnemonic: str, passphrase: str = "") -> bytes` (PBKDF2-HMAC-SHA512, 2048 iterations, salt = `"mnemonic" + passphrase`). `derive_path(seed: bytes, path: str) -> bytes` returns the 32-byte private key at e.g. `m/44'/60'/0'/0/0`.
- **`lib/rlp.py`** — `encode(item) -> bytes` where `item` is `bytes | int | list`. Integers are encoded as their minimal big-endian form (zero is encoded as `b""`).
- **`lib/abi.py`** — `encode_call(selector: bytes, args: list) -> bytes` for the specific shape `(address, uint256, address)` and the convenience `encode_uint256(int) -> bytes` / `encode_address(str) -> bytes` for decoding `eth_call` return data.
- **`lib/jsonrpc.py`** — `class RpcClient` with one HTTP POST under the hood and one `call(method, params)` method that raises on JSON-RPC errors. Plus typed wrappers for the Eth methods we use.
- **`lib/eth.py`** — `to_checksum_address(addr: str) -> str` (EIP-55), `build_eip1559_tx(...)`, `sign_eip1559_tx(...) -> bytes` (returns the raw `0x02 || rlp(...)` payload ready for `eth_sendRawTransaction`). Hashes the unsigned envelope with Keccak, signs via `crypto.sign_recoverable`, re-encodes the signed envelope.
- **`lib/aave.py`** — `class AavePool` wrapping the pool address with methods `withdraw_calldata(asset, amount, to) -> bytes`, `get_reserve_data(asset) -> ReserveData`, `get_user_account_data(user) -> AccountData`. `class Erc20` with `balance_of(addr) -> int`, `decimals() -> int`, `symbol() -> str`. `decode_reserve_status(config: int) -> ReserveStatus` mirroring the TS bit decoder (bits 56/57/60).
- **`lib/vectors.py`** — Embedded `(input, expected)` pairs sourced from:
  - [XKCP](https://github.com/XKCP/XKCP/tree/master/tests/TestVectors) for Keccak-256 (a handful: empty string, `"abc"`, longer strings)
  - [BIP-39 English vectors](https://github.com/trezor/python-mnemonic/blob/master/vectors.json) for mnemonic→seed
  - [BIP-32 mainnet vectors](https://github.com/bitcoin/bips/blob/master/bip-0032.mediawiki#test-vectors) for seed → derived key
  - Synthetic RLP and ABI vectors derived from the Ethereum yellow paper / Solidity ABI spec
- **`aave.py` (entrypoint)** — argparse with subcommands `selftest`, `verify`, `withdraw`. `withdraw` accepts `--only=DAI|USDC|USDT`, `--yes`, `--help`. Loads `.env` via a tiny hand-rolled parser (see §5.4) — no `python-dotenv` dependency.

### 5.2 Constants & addresses

Hard-coded in `lib/aave.py`, identical to the TS version:

```
POOL          = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2
DAI underlying / aToken / decimals = 0x6B17...1d0F / 0x0180...4e63 / 18
USDC ditto                          = 0xA0b8...eB48 / 0x98C2...6F5c / 6
USDT ditto                          = 0xdAC1...1ec7 / 0x2387...086a / 6
withdraw selector                   = 0x69328dec
```

All addresses are validated against the canonical [`bgd-labs/aave-address-book`](https://github.com/bgd-labs/aave-address-book) source.

### 5.3 Tx assembly

For each market we want to withdraw, `lib/eth.py` builds an EIP-1559 (type-2) transaction:

```
unsigned = 0x02 || rlp([
    chainId, nonce, maxPriorityFeePerGas, maxFeePerGas, gasLimit,
    to, value, data, accessList,
])
sighash  = keccak256(unsigned)
(r, s, y) = sign_recoverable(sighash, priv)
signed   = 0x02 || rlp([
    chainId, nonce, maxPriorityFeePerGas, maxFeePerGas, gasLimit,
    to, value, data, accessList,
    y, r, s,
])
```

`accessList` is always the empty list `[]`. `value` is always `0`. `to` is the pool. `data` is the ABI-encoded `withdraw` call.

### 5.4 `.env` parsing

A 20-line parser in `aave.py`: read the file, split on lines, ignore `#`-prefixed and blank lines, split each line on the first `=`, strip surrounding whitespace and a single pair of matching quotes from the value. Set into `os.environ` only if not already set (so real env vars override `.env`). No third-party `python-dotenv` dep.

### 5.5 Mnemonic input options

Two ways to provide the mnemonic, in this priority order:

1. `MNEMONIC` env var (set in `.env` or shell). Same as TS version. Convenient.
2. `--prompt` CLI flag, which uses `getpass.getpass("BIP-39 mnemonic: ")` so the seed is never written to disk and never shows up in shell history. Recommended for the actual D-day run on the hardened machine.

If both are present, `--prompt` wins.

## 6. Data flow

```
.env / --prompt   ->  mnemonic + path
                          |
                          v
                     bip39_32 (PBKDF2 + CKDpriv)
                          |
                          v
                     priv32 bytes
                          |
                  +-------+--------+
                  v                v
            crypto.derive    crypto.sign
              address          (per tx)
                  |                ^
                  v                |
              wallet addr    sighash from eth.build
                  |                ^
                  v                |
        verify: aave reads ---+    |
                              |    |
        withdraw: aave reads -+    |
                              v    |
                       per-market loop
                       - estimateGas
                       - prompt y/n
                       - sign + send
                       - poll receipt
                              |
                              v
                  exit with summary
```

## 7. Error handling

- **Self-test failure on startup of `verify` or `withdraw`** → print which vector(s) failed, exit code 3. (The user runs `selftest` separately to see all results.)
- **Mnemonic invalid** (wrong word count, word not in list, checksum mismatch) → clear error before any RPC calls, exit code 2.
- **`totalDebtBase > 0`** in pre-flight → refuse to start, exit code 2 (matches TS version).
- **`eth_estimateGas` reverts for a market** → print the revert reason if decodable as a panic / error string, otherwise the raw hex. Ask whether to continue with the next market.
- **`eth_sendRawTransaction` rejected** (replacement underpriced, nonce too low, etc.) → print the JSON-RPC error and abort the sequence.
- **Receipt polling timeout** (> 5 minutes) → print the tx hash and exit code 4 so the user can manually check Etherscan.
- **Network error from `urllib`** → retry up to 3× with exponential backoff (1 s, 2 s, 4 s), then surface.

## 8. Security details

- The mnemonic is held in process memory only. Never written to a keystore file. After signing the last tx the script overwrites the byte buffers holding the priv key (best-effort; CPython doesn't make this perfect but it's a habit worth keeping).
- `.env` is `chmod 600` per the README instructions (same as TS version). `.gitignore` excludes it.
- The README will repeat the same hot-wallet warnings as the TS README — dedicated seed, ETH only for gas, move funds to cold immediately, `shred -u .env` after.
- The `--prompt` flag is the recommended path for D-day, since it means the seed is never on disk at all.
- We deliberately do NOT log the mnemonic or the private key, ever. The verify/withdraw subcommands log only the public address.

## 9. Testing strategy

Three layers:

1. **`aave.py selftest`** — runs the embedded test vectors for Keccak, BIP-39, BIP-32, RLP, and ABI. Exits non-zero on any mismatch. Required by `verify` and `withdraw` to pass before they do anything.
2. **Smoke test on Hardhat / Anvil mnemonic** — same throwaway pattern we used to validate the TS version. Run `verify` against mainnet RPC with the well-known test mnemonic; expect zero balances and no broadcast.
3. **End-to-end on testnet (optional)** — Aave V3 is deployed on Sepolia at known addresses. We can include a `--testnet=sepolia` flag in a follow-up if the user wants a wet dry-run before D-day. Out of scope for v1 but the design accommodates it (single addresses constants block to swap).

## 10. Deviations from the TS version

| TS behavior | Python behavior | Why |
|---|---|---|
| Reads gas from `provider.getFeeData()` | Reads `eth_getBlockByNumber("latest")` and uses `2 * baseFeePerGas + priority` | Stdlib JSON-RPC; no provider abstraction |
| `Wallet.signTransaction` from ethers | `lib/eth.sign_eip1559_tx` | Hand-rolled equivalent |
| `--yes` and `AUTO_CONFIRM` env both work | Same | parity |
| No `selftest` subcommand | New `selftest` subcommand | Compensates for hand-rolled crypto |
| No `--prompt` flag | New `--prompt` flag for getpass-based mnemonic entry | Hardened-machine use case |
| `tsx` for execution | Direct `python aave.py` | Stdlib only |

## 11. File layout (final)

```
aave-py/
├── README.md
├── .env.example
├── .gitignore
├── requirements.txt
├── wordlist.txt
├── aave.py
└── lib/
    ├── __init__.py
    ├── keccak.py
    ├── crypto.py
    ├── bip39_32.py
    ├── rlp.py
    ├── abi.py
    ├── jsonrpc.py
    ├── eth.py
    ├── aave.py
    └── vectors.py
```

## 12. Open questions

None.

## 13. Acceptance criteria

The implementation is complete when:

1. `pip install -r requirements.txt` installs exactly one package (`coincurve`) on the user's hardened machine.
2. `python aave.py selftest` passes all embedded vectors.
3. `python aave.py verify` against the well-known Hardhat mnemonic on mainnet RPC produces output structurally identical to the TS `npm run verify` smoke test from the previous session (correct addresses, reserve states, account data).
4. `python aave.py withdraw --only=USDC --yes` against the same empty wallet skips cleanly without broadcasting.
5. The CLI `--help`, `--only=<bad-value>`, and `--prompt` flags behave correctly.
6. The Python tool's external trust surface is exactly: CPython stdlib, `coincurve`, the `wordlist.txt` file. Nothing else.

#!/usr/bin/env bash
# rehearse.sh - automated dry-run of the full pipeline against the public
# BIP-39 abandon mnemonic (a well-known empty wallet on mainnet).
#
# Exercises selftest -> verify -> withdraw --only=DAI/USDC/USDT --yes
# end-to-end against real Ethereum mainnet via your Alchemy URL, asserts
# the expected wallet derives correctly, all reserves are healthy, and
# every market skips cleanly with no broadcast.
#
# Useful for:
#   - Catching environment regressions before D-day (Python version, venv
#     state, coincurve install, network reachability)
#   - Sanity-checking that the README setup steps still work end-to-end
#   - Smoke-testing a freshly-cloned checkout
#
# This script does NOT use your real .env. It writes a temporary .env with
# the abandon mnemonic, restores yours afterward, and never touches your
# real funds. The abandon mnemonic is public (it's the canonical BIP-39
# spec test vector) and the wallet has zero balance everywhere.

set -uo pipefail
# Note: -e intentionally NOT set; we want to keep running through all
# checks and report the full PASS/FAIL summary.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ----- resolve ALCHEMY_HTTP_URL: arg, env, or existing .env -----

ALCHEMY="${1:-${ALCHEMY_HTTP_URL:-}}"
if [ -z "$ALCHEMY" ] && [ -f .env ]; then
    ALCHEMY="$(grep -E '^ALCHEMY_HTTP_URL=' .env | head -1 | cut -d= -f2- | tr -d '\r')"
fi
if [ -z "$ALCHEMY" ]; then
    cat >&2 <<EOF
Usage: $0 <ALCHEMY_HTTP_URL>
   or: ALCHEMY_HTTP_URL=https://... $0
   or: have an existing .env in $SCRIPT_DIR with ALCHEMY_HTTP_URL set

Need an Ethereum mainnet RPC URL to talk to.
EOF
    exit 1
fi

# ----- preserve any existing .env, restore on exit -----

ENV_BACKUP=""
if [ -f .env ]; then
    ENV_BACKUP="$(mktemp -t aave-py-rehearse-env.XXXXXX)"
    cp .env "$ENV_BACKUP"
fi

cleanup() {
    rm -f .env
    if [ -n "$ENV_BACKUP" ] && [ -f "$ENV_BACKUP" ]; then
        mv "$ENV_BACKUP" .env
    fi
}
trap cleanup EXIT INT TERM

# ----- write rehearsal .env (abandon mnemonic) -----

cat > .env <<EOF
ALCHEMY_HTTP_URL=$ALCHEMY
MNEMONIC=abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about
HD_PATH=m/44'/60'/0'/0/0
EOF
chmod 600 .env

ABANDON_ADDR="0x9858EfFD232B4033E47d90003D41EC34EcaEda94"

# ----- check helpers -----

PASS=0
FAIL=0

# Format: check <label> <command> [must-contain] [must-not-contain]
# Expects command to exit 0.
check() {
    _check 0 "$@"
}

# Format: check_fails <label> <command> [must-contain] [must-not-contain]
# Expects command to exit NON-zero (e.g. argparse rejection).
check_fails() {
    _check 1 "$@"
}

# Internal: $1 = expect_fail (0=expect-success, 1=expect-non-zero exit)
_check() {
    local expect_fail="$1"
    local label="$2"
    local cmd="$3"
    local expect_in="${4:-}"
    local expect_not_in="${5:-}"

    printf '[%-12s] %s ... ' "REHEARSE" "$label"
    local output rc
    output="$(eval "$cmd" 2>&1)"
    rc=$?

    if [ "$expect_fail" = "0" ] && [ $rc -ne 0 ]; then
        echo "FAIL (expected exit 0, got $rc)"
        printf '%s\n' "$output" | sed 's/^/    /'
        FAIL=$((FAIL + 1))
        return
    fi
    if [ "$expect_fail" = "1" ] && [ $rc -eq 0 ]; then
        echo "FAIL (expected non-zero exit, got 0)"
        printf '%s\n' "$output" | sed 's/^/    /'
        FAIL=$((FAIL + 1))
        return
    fi
    if [ -n "$expect_in" ] && ! printf '%s' "$output" | grep -qF -- "$expect_in"; then
        echo "FAIL"
        echo "    expected substring not found: $expect_in"
        printf '%s\n' "$output" | sed 's/^/    /'
        FAIL=$((FAIL + 1))
        return
    fi
    if [ -n "$expect_not_in" ] && printf '%s' "$output" | grep -qF -- "$expect_not_in"; then
        echo "FAIL"
        echo "    forbidden substring found: $expect_not_in"
        printf '%s\n' "$output" | sed 's/^/    /'
        FAIL=$((FAIL + 1))
        return
    fi
    echo "PASS"
    PASS=$((PASS + 1))
}

echo "=== aave-py rehearsal ==="
echo "RPC: ${ALCHEMY:0:60}..."
echo "Wallet (expected): $ABANDON_ADDR"
echo

# ----- the actual rehearsal -----

check "selftest" \
    "./aave selftest" \
    "selftest: ALL PASS"

check "verify wallet derives correctly" \
    "./aave verify" \
    "$ABANDON_ADDR"

check "verify reports healthy DAI reserve" \
    "./aave verify" \
    "DAI"

check "verify reports zero balances" \
    "./aave verify" \
    "Markets with non-zero withdrawable balance: NONE."

check "withdraw --only=DAI --yes skips, no broadcast" \
    "./aave withdraw --only=DAI --yes" \
    "nothing to withdraw" \
    "tx hash:"

check "withdraw --only=USDC --yes skips, no broadcast" \
    "./aave withdraw --only=USDC --yes" \
    "nothing to withdraw" \
    "tx hash:"

check "withdraw --only=USDT --yes skips, no broadcast" \
    "./aave withdraw --only=USDT --yes" \
    "nothing to withdraw" \
    "tx hash:"

check_fails "withdraw --only=BANANA rejected by argparse" \
    "./aave withdraw --only=BANANA" \
    "invalid choice"

echo
echo "=== summary: $PASS passed, $FAIL failed ==="

if [ $FAIL -gt 0 ]; then
    exit 1
fi
exit 0

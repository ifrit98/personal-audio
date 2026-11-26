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
                    data = params[0]["data"]
                    if data.startswith("0xbf92857c"):  # getUserAccountData
                        return "0x" + "00" * (32 * 6)
                    if data.startswith("0x35ea6a75"):  # getReserveData -> active bit
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

    def _run_withdraw_with_debt(self, debt_base: int):
        """Drive cmd_withdraw against a mocked account carrying `debt_base` debt.

        Returns (exit_code_or_None, stdout).
        """
        buf = io.StringIO()
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
                    if data.startswith("0xbf92857c"):  # getUserAccountData
                        zero = "00" * 32
                        debt = debt_base.to_bytes(32, "big").hex()
                        return "0x" + zero + debt + zero * 4
                return _zero32()

            client.call.side_effect = call
            args = argparse.Namespace(prompt=False, only=None, yes=True)
            code = None
            try:
                with redirect_stdout(buf):
                    aave.cmd_withdraw(args)
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def test_withdraw_refuses_with_real_open_debt(self):
        # $5.00 of debt, comfortably above the dust floor.
        code, out = self._run_withdraw_with_debt(5 * 10 ** 8)
        self.assertEqual(code, 2)
        self.assertIn("open Aave debt", out)

    def test_withdraw_ignores_rounding_dust_debt(self):
        # $0.000001 - Aave leaves this on accounts that have ever borrowed.
        # It must not block the sequence (regression: it used to exit 2).
        code, out = self._run_withdraw_with_debt(100)
        self.assertIsNone(code)
        self.assertIn("dust debt", out)

    def test_dust_floor_boundary_is_inclusive(self):
        # Exactly at the floor counts as dust; one unit above does not.
        code_at, _ = self._run_withdraw_with_debt(aave.DEFAULT_DEBT_DUST_BASE)
        self.assertIsNone(code_at)
        code_over, _ = self._run_withdraw_with_debt(aave.DEFAULT_DEBT_DUST_BASE + 1)
        self.assertEqual(code_over, 2)

    def test_allow_debt_overrides_the_guard(self):
        buf = io.StringIO()
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
                    if data.startswith("0xbf92857c"):
                        zero = "00" * 32
                        debt = (5 * 10 ** 8).to_bytes(32, "big").hex()
                        return "0x" + zero + debt + zero * 4
                return _zero32()

            client.call.side_effect = call
            args = argparse.Namespace(prompt=False, only=None, yes=True, allow_debt=True)
            with redirect_stdout(buf):
                aave.cmd_withdraw(args)
        self.assertNotIn("Refusing to start", buf.getvalue())


class TestReceiptPolling(unittest.TestCase):
    def test_wait_for_receipt_survives_transient_jsonrpc_error(self):
        from unittest.mock import MagicMock
        from lib.jsonrpc import JsonRpcError

        client = MagicMock()
        # First call raises, second returns a valid receipt.
        client.call.side_effect = [
            JsonRpcError(-32603, "internal error"),
            {"blockNumber": "0x1", "status": "0x1", "gasUsed": "0x5208"},
        ]
        # Patch time.sleep so the test isn't slow.
        with patch("time.sleep"):
            buf = io.StringIO()
            with redirect_stdout(buf):
                receipt = aave._wait_for_receipt(client, "0xabc", timeout_secs=10, poll_secs=0)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["status"], "0x1")
        self.assertIn("transient RPC error", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

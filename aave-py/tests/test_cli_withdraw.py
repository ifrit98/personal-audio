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

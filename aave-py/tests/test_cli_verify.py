import argparse
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import aave


class TestVerifyCommand(unittest.TestCase):
    def test_verify_prints_per_market_lines(self):
        # Mock the RPC layer so this test never touches the network.
        # The mock differentiates by method AND, for eth_call, by the calldata
        # selector prefix because cmd_verify makes several different eth_calls.
        with patch.object(aave, "_resolve_mnemonic", return_value=(
            "abandon abandon abandon abandon abandon abandon abandon abandon "
            "abandon abandon abandon about"
        )), patch.object(aave, "_make_client") as make_client:
            client = make_client.return_value

            def fake_call(method, params):
                if method == "eth_blockNumber":
                    return "0x10"
                if method == "eth_getBalance":
                    return "0x0"
                if method == "eth_getTransactionCount":
                    return "0x0"
                if method == "eth_call":
                    data = params[0]["data"]
                    # getUserAccountData: 6 uint256 zeros
                    if data.startswith("0xbf92857c"):
                        return "0x" + "00" * (32 * 6)
                    # symbol(): dynamic string "TKN"
                    if data.startswith("0x95d89b41"):
                        offset = (32).to_bytes(32, "big").hex()
                        length = (3).to_bytes(32, "big").hex()
                        payload = b"TKN".ljust(32, b"\x00").hex()
                        return "0x" + offset + length + payload
                    # getReserveData: first 32 bytes is configuration with active bit set
                    if data.startswith("0x35ea6a75"):
                        first_word = (1 << 56).to_bytes(32, "big").hex()
                        return "0x" + first_word + "00" * (32 * 14)
                    # balanceOf: 32 zero bytes
                    return "0x" + "00" * 32
                raise AssertionError(f"unexpected method: {method}")

            client.call.side_effect = fake_call

            args = argparse.Namespace(prompt=False)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = aave.cmd_verify(args)
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("DAI", out)
        self.assertIn("USDC", out)
        self.assertIn("USDT", out)
        # Wallet line should show the abandon-mnemonic-derived address.
        self.assertIn("0x9858EfFD232B4033E47d90003D41EC34EcaEda94", out)


class TestVerifyBadMnemonicExits2(unittest.TestCase):
    def test_bad_mnemonic_exits_2_with_clean_message(self):
        from unittest.mock import patch
        with patch.object(
            aave, "_resolve_mnemonic",
            return_value="totally bogus words and not bip39 valid at all",
        ), patch.object(aave, "_make_client"):
            buf = io.StringIO()
            with redirect_stdout(buf), self.assertRaises(SystemExit) as cm:
                args = argparse.Namespace(prompt=False)
                aave.cmd_verify(args)
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("Mnemonic", buf.getvalue())
        # Should NOT contain a Python traceback marker
        self.assertNotIn("Traceback", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

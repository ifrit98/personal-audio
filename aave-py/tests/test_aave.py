import unittest
import unittest.mock

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

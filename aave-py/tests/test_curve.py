"""Tests for Curve 3pool constants, calldata, and the slippage guard.

Selectors and the coin layout are pinned against values read from the live
mainnet contract at 0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7.
"""

import unittest
from unittest.mock import MagicMock

from lib import abi, curve


class TestSelectors(unittest.TestCase):
    """Recomputed from signatures; these hex values were confirmed on-chain."""

    def test_selectors_match_onchain_values(self):
        self.assertEqual(curve.GET_DY_SELECTOR.hex(), "5e0d443f")
        self.assertEqual(curve.EXCHANGE_SELECTOR.hex(), "3df02124")
        self.assertEqual(curve.COINS_SELECTOR.hex(), "c6610657")
        self.assertEqual(curve.FEE_SELECTOR.hex(), "ddca3f43")
        self.assertEqual(curve.BALANCES_SELECTOR.hex(), "4903b0d1")

    def test_erc20_selectors(self):
        self.assertEqual(curve.APPROVE_SELECTOR.hex(), "095ea7b3")
        self.assertEqual(curve.ALLOWANCE_SELECTOR.hex(), "dd62ed3e")


class TestCoinLayout(unittest.TestCase):
    def test_indices_match_the_live_pool(self):
        self.assertEqual(curve.COINS["DAI"].index, 0)
        self.assertEqual(curve.COINS["USDC"].index, 1)
        self.assertEqual(curve.COINS["USDT"].index, 2)

    def test_decimals(self):
        self.assertEqual(curve.COINS["DAI"].decimals, 18)
        self.assertEqual(curve.COINS["USDC"].decimals, 6)
        self.assertEqual(curve.COINS["USDT"].decimals, 6)

    def test_assert_coin_layout_accepts_correct_ordering(self):
        client = MagicMock()

        def call(method, params):
            index = int(params[0]["data"][10:], 16)
            addr = {0: curve.DAI, 1: curve.USDC, 2: curve.USDT}[index]
            return "0x" + "00" * 12 + addr[2:].lower()

        client.call.side_effect = call
        curve.assert_coin_layout(client)  # must not raise

    def test_assert_coin_layout_refuses_on_mismatch(self):
        client = MagicMock()

        def call(method, params):
            # Pool reports USDC where we expect DAI.
            return "0x" + "00" * 12 + curve.USDC[2:].lower()

        client.call.side_effect = call
        with self.assertRaises(RuntimeError) as cm:
            curve.assert_coin_layout(client)
        self.assertIn("mismatch", str(cm.exception))


class TestCalldata(unittest.TestCase):
    def test_exchange_calldata_shape(self):
        # USDC (1) -> DAI (0), 125_000.006159 USDC in, 124_000e18 min out.
        dx = 125_000_006159
        min_dy = 124_000 * 10 ** 18
        data = curve.exchange_calldata(1, 0, dx, min_dy)
        self.assertEqual(len(data), 4 + 32 * 4)
        self.assertEqual(data[:4].hex(), "3df02124")
        self.assertEqual(abi.decode_uint256(data[4:36]), 1)
        self.assertEqual(abi.decode_uint256(data[36:68]), 0)
        self.assertEqual(abi.decode_uint256(data[68:100]), dx)
        self.assertEqual(abi.decode_uint256(data[100:132]), min_dy)

    def test_get_dy_calldata_shape(self):
        data = curve.get_dy_calldata(2, 0, 1_000_000)
        self.assertEqual(len(data), 4 + 32 * 3)
        self.assertEqual(data[:4].hex(), "5e0d443f")
        self.assertEqual(abi.decode_uint256(data[4:36]), 2)
        self.assertEqual(abi.decode_uint256(data[68:100]), 1_000_000)

    def test_approve_calldata_targets_the_pool(self):
        data = curve.approve_calldata(curve.POOL, 12345)
        self.assertEqual(data[:4].hex(), "095ea7b3")
        self.assertEqual(abi.decode_address(data[4:36]).lower(), curve.POOL.lower())
        self.assertEqual(abi.decode_uint256(data[36:68]), 12345)

    def test_allowance_calldata_orders_owner_then_spender(self):
        owner = "0x" + "11" * 20
        data = curve.allowance_calldata(owner, curve.POOL)
        self.assertEqual(abi.decode_address(data[4:36]).lower(), owner.lower())
        self.assertEqual(abi.decode_address(data[36:68]).lower(), curve.POOL.lower())


class TestEncodeInt128(unittest.TestCase):
    def test_zero_and_positive(self):
        self.assertEqual(abi.encode_int128(0).hex(), "00" * 32)
        self.assertEqual(abi.encode_int128(2).hex(), "00" * 31 + "02")

    def test_negative_uses_twos_complement(self):
        self.assertEqual(abi.encode_int128(-1).hex(), "ff" * 32)

    def test_range_enforced(self):
        with self.assertRaises(ValueError):
            abi.encode_int128(1 << 127)
        with self.assertRaises(ValueError):
            abi.encode_int128(-(1 << 127) - 1)

    def test_rejects_bool(self):
        with self.assertRaises(TypeError):
            abi.encode_int128(True)


class TestSlippageGuard(unittest.TestCase):
    def test_ten_bps_off_a_realistic_quote(self):
        quoted = 124_981_142393_000_000_000_000  # ~124,981.14 DAI
        self.assertEqual(
            curve.min_dy_with_slippage(quoted, 10),
            quoted * 9990 // 10_000,
        )

    def test_zero_slippage_demands_the_full_quote(self):
        self.assertEqual(curve.min_dy_with_slippage(1000, 0), 1000)

    def test_guard_never_exceeds_the_quote(self):
        for bps in (0, 1, 10, 50, 100, 10_000):
            with self.subTest(bps=bps):
                self.assertLessEqual(curve.min_dy_with_slippage(12345, bps), 12345)

    def test_full_slippage_floors_at_zero(self):
        self.assertEqual(curve.min_dy_with_slippage(12345, 10_000), 0)

    def test_rejects_out_of_range_bps(self):
        with self.assertRaises(ValueError):
            curve.min_dy_with_slippage(1000, -1)
        with self.assertRaises(ValueError):
            curve.min_dy_with_slippage(1000, 10_001)


class TestReads(unittest.TestCase):
    def test_get_dy_decodes_uint256(self):
        client = MagicMock()
        client.call.return_value = "0x" + (999).to_bytes(32, "big").hex()
        self.assertEqual(curve.get_dy(client, 1, 0, 100), 999)

    def test_fee_denominator_matches_onchain_scale(self):
        # Live pool reported fee() == 1500000, i.e. 0.015%.
        self.assertAlmostEqual(1_500_000 / curve.FEE_DENOMINATOR, 0.00015)

    def test_unexpected_call_return_raises(self):
        client = MagicMock()
        client.call.return_value = None
        with self.assertRaises(RuntimeError):
            curve.get_dy(client, 1, 0, 100)


if __name__ == "__main__":
    unittest.main()

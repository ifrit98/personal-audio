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

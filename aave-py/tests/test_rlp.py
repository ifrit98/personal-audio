import unittest

from lib.rlp import encode


class TestRlp(unittest.TestCase):
    """Vectors from the Ethereum yellow paper appendix and the RLP wiki."""

    def test_empty_string(self):
        self.assertEqual(encode(b"").hex(), "80")

    def test_zero_byte(self):
        # Single byte < 0x80 encodes as itself.
        self.assertEqual(encode(b"\x00").hex(), "00")

    def test_single_byte_below_threshold(self):
        self.assertEqual(encode(b"\x0f").hex(), "0f")

    def test_short_string(self):
        # "dog" -> 83 64 6f 67
        self.assertEqual(encode(b"dog").hex(), "8364 6f67".replace(" ", ""))

    def test_55_byte_string(self):
        # 55 bytes is the boundary for short-string encoding.
        s = b"L" * 55
        self.assertEqual(encode(s).hex(), "b7" + "4c" * 55)

    def test_56_byte_string(self):
        # 56 bytes -> long-string form: 0xb8 (1 byte length prefix), 0x38 length, payload.
        s = b"L" * 56
        self.assertEqual(encode(s).hex(), "b838" + "4c" * 56)

    def test_empty_list(self):
        self.assertEqual(encode([]).hex(), "c0")

    def test_short_list(self):
        # ["cat", "dog"] -> c8 83 63 61 74 83 64 6f 67
        self.assertEqual(
            encode([b"cat", b"dog"]).hex(),
            "c883636174 83646f67".replace(" ", ""),
        )

    def test_int_zero(self):
        # 0 encodes as empty bytes -> 0x80
        self.assertEqual(encode(0).hex(), "80")

    def test_int_15(self):
        self.assertEqual(encode(15).hex(), "0f")

    def test_int_1024(self):
        # 1024 = 0x0400 -> 82 04 00
        self.assertEqual(encode(1024).hex(), "820400")

    def test_nested_list(self):
        # [[], [[]], [[], [[]]]] -> the "set theoretic representation of three"
        # Per yellow paper appendix.
        self.assertEqual(encode([[], [[]], [[], [[]]]]).hex(), "c7c0c1c0c3c0c1c0")


if __name__ == "__main__":
    unittest.main()

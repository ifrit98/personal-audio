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

    def test_list_55_byte_payload(self):
        # Payload of exactly 55 bytes -> short-list form: 0xc0+55 = 0xf7, then payload.
        # 11 strings of 4 bytes each: each string encodes as (0x84 | "AAAA") = 5 bytes,
        # so 11 * 5 = 55 bytes payload exactly.
        items = [b"AAAA"] * 11
        out = encode(items)
        self.assertEqual(out[0], 0xf7)
        self.assertEqual(len(out), 56)  # 1 header byte + 55 payload bytes

    def test_list_56_byte_payload(self):
        # Payload of 56 bytes -> long-list form: 0xf7+1 = 0xf8, then 0x38 (=56), then payload.
        # 11 strings of 4 bytes (= 55) + one 1-byte string with header (= 1+1 = 2... too much).
        # Easier: build 56 bytes via one string whose RLP encoding is 56 bytes:
        # a 54-byte string encodes as 0x80+54 (= 0xb6) + payload = 55 bytes. Add one more
        # 1-byte item: 1 byte (since 0x00..0x7f is a passthrough). So [b"X"*54, b"Y"] gives
        # 55 + 1 = 56 bytes payload.
        items = [b"X" * 54, b"Y"]
        out = encode(items)
        self.assertEqual(out[0], 0xf8)
        self.assertEqual(out[1], 56)
        self.assertEqual(len(out), 2 + 56)  # 2 header bytes + 56 payload bytes

    def test_list_long_payload_lenoflen(self):
        # Payload large enough to require a 2-byte length encoding (>= 256 bytes).
        # One 300-byte string encodes as 0xb8 0x2c (=44) ... wait, 300 needs 2 bytes for length:
        # 0xb9 0x01 0x2c + payload = 3 + 300 = 303 bytes payload. List header: 0xf7+2 = 0xf9,
        # then 0x01 0x2f (=303 in big-endian).
        out = encode([b"X" * 300])
        self.assertEqual(out[0], 0xf9)
        # Big-endian length of payload (303 = 0x012f):
        self.assertEqual(out[1:3], bytes.fromhex("012f"))
        self.assertEqual(len(out), 3 + 303)


if __name__ == "__main__":
    unittest.main()

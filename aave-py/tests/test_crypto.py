import unittest

from lib.crypto import sign_recoverable, derive_address


# Famous EIP-155 example private key.
PRIV = bytes.fromhex("4646464646464646464646464646464646464646464646464646464646464646")
EXPECTED_ADDR = "0x9d8A62f656a8d1615C1294fd71e9CFb3E4855A4F"


class TestCrypto(unittest.TestCase):
    def test_derive_address_eip155_example(self):
        self.assertEqual(derive_address(PRIV), EXPECTED_ADDR)

    def test_sign_recoverable_returns_canonical_y(self):
        msg = bytes.fromhex("00" * 32)  # any deterministic 32-byte digest works
        r, s, y = sign_recoverable(PRIV, msg)
        # secp256k1 group order n
        n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
        self.assertTrue(0 < r < n)
        # coincurve returns "low-s" canonical form
        self.assertTrue(0 < s <= n // 2, "signature s must be canonically low")
        self.assertIn(y, (0, 1))

    def test_sign_recovers_to_same_address(self):
        from coincurve import PublicKey
        from lib.keccak import keccak256

        msg = bytes.fromhex("11" * 32)
        r, s, y = sign_recoverable(PRIV, msg)
        sig65 = r.to_bytes(32, "big") + s.to_bytes(32, "big") + bytes([y])
        recovered = PublicKey.from_signature_and_message(sig65, msg, hasher=None)
        uncompressed = recovered.format(compressed=False)
        addr = "0x" + keccak256(uncompressed[1:])[12:].hex()
        self.assertEqual(addr.lower(), EXPECTED_ADDR.lower())


if __name__ == "__main__":
    unittest.main()

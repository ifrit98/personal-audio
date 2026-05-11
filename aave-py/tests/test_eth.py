import unittest

from coincurve import PublicKey

from lib.eth import to_checksum_address, build_eip1559_tx, sign_eip1559_tx, sighash_eip1559
from lib.keccak import keccak256


# Wallet derived from the BIP-39 "abandon x11 about" mnemonic at
# m/44'/60'/0'/0/0. (Cross-verified in Task 8.)
ABANDON_PRIV = bytes.fromhex("1ab42cc412b618bdea3a599e3c9bae199ebf030895b039e9db1e30dafb12b727")
ABANDON_ADDR = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"


class TestEip55(unittest.TestCase):
    """Vectors from EIP-55 itself."""

    def test_all_lower_input(self):
        self.assertEqual(
            to_checksum_address("0xfb6916095ca1df60bb79ce92ce3ea74c37c5d359"),
            "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
        )

    def test_all_caps_short(self):
        self.assertEqual(
            to_checksum_address("0x52908400098527886e0f7030069857d2e4169ee7"),
            "0x52908400098527886E0F7030069857D2E4169EE7",
        )

    def test_already_checksummed_idempotent(self):
        addr = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
        self.assertEqual(to_checksum_address(addr), addr)


class TestEip1559Signing(unittest.TestCase):
    def test_round_trip(self):
        tx = build_eip1559_tx(
            chain_id=1,
            nonce=0,
            max_priority_fee_per_gas=2_000_000_000,
            max_fee_per_gas=50_000_000_000,
            gas_limit=500_000,
            to="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
            value=0,
            data=bytes.fromhex("69328dec"),
        )
        sighash = sighash_eip1559(tx)
        raw = sign_eip1559_tx(tx, ABANDON_PRIV)
        # Raw must start with the 0x02 type byte.
        self.assertEqual(raw[0], 0x02)

        # Recover the signer from sighash + sig and confirm == ABANDON_ADDR.
        # Sign again and pull (r,s,y) from a second call rather than parsing
        # them out of the signed RLP envelope.
        from lib.crypto import sign_recoverable
        r, s, y = sign_recoverable(ABANDON_PRIV, sighash)
        sig65 = r.to_bytes(32, "big") + s.to_bytes(32, "big") + bytes([y])
        recovered = PublicKey.from_signature_and_message(sig65, sighash, hasher=None)
        addr = "0x" + keccak256(recovered.format(compressed=False)[1:])[12:].hex()
        self.assertEqual(addr.lower(), ABANDON_ADDR.lower())


if __name__ == "__main__":
    unittest.main()

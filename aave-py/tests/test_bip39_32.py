import unittest

from lib.bip39_32 import mnemonic_to_seed, derive_path
from lib.crypto import derive_address


# BIP-39 official test vector: 12 words "abandon" x11 "about", no passphrase.
ABANDON = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
ABANDON_SEED_HEX = (
    "5eb00bbddcf069084889a8ab9155568165f5c453ccb85e70811aaed6f6da5fc1"
    "9a5ac40b389cd370d086206dec8aa6c43daea6690f20ad3d8d48b2d2ce9e38e4"
)

# Canonical BIP-44 derivation from the BIP-39 "abandon x11 about" mnemonic at
# m/44'/60'/0'/0/0. Cross-checked against MetaMask, ethers.js, and web3.py.
#
# NOTE: This is NOT the Hardhat/Anvil default address. Hardhat/Anvil's default
# first account `0xf39Fd6...` comes from the test mnemonic
# "test test test test test test test test test test test junk", not from the
# abandon mnemonic.
ABANDON_PRIV = bytes.fromhex("1ab42cc412b618bdea3a599e3c9bae199ebf030895b039e9db1e30dafb12b727")
ABANDON_ADDR = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"


class TestBip39(unittest.TestCase):
    def test_mnemonic_to_seed_abandon(self):
        seed = mnemonic_to_seed(ABANDON)
        self.assertEqual(seed.hex(), ABANDON_SEED_HEX)

    def test_mnemonic_to_seed_with_passphrase(self):
        # Trezor official vector: same mnemonic + passphrase "TREZOR".
        seed = mnemonic_to_seed(ABANDON, passphrase="TREZOR")
        self.assertEqual(
            seed.hex(),
            "c55257c360c07c72029aebc1b53c05ed0362ada38ead3e3e9efa3708e5349553"
            "1f09a6987599d18264c1e1c92f2cf141630c7a3c4ab7c81b2f001698e7463b04",
        )

    def test_invalid_word_rejected(self):
        bad = ABANDON.replace("about", "abouttt")
        with self.assertRaises(ValueError):
            mnemonic_to_seed(bad)

    def test_bad_checksum_rejected(self):
        # Swap the last word "about" (idx 3) for "abandon" (idx 0); checksum will mismatch.
        bad = ABANDON.replace("about", "abandon")
        with self.assertRaises(ValueError):
            mnemonic_to_seed(bad)

    def test_wrong_word_count_rejected(self):
        with self.assertRaises(ValueError):
            mnemonic_to_seed("abandon abandon abandon")


class TestBip32(unittest.TestCase):
    def test_derive_path_yields_abandon_priv(self):
        seed = mnemonic_to_seed(ABANDON)
        priv = derive_path(seed, "m/44'/60'/0'/0/0")
        self.assertEqual(priv.hex(), ABANDON_PRIV.hex())

    def test_derive_path_address(self):
        seed = mnemonic_to_seed(ABANDON)
        priv = derive_path(seed, "m/44'/60'/0'/0/0")
        self.assertEqual(derive_address(priv), ABANDON_ADDR)

    def test_path_must_start_with_m(self):
        seed = mnemonic_to_seed(ABANDON)
        with self.assertRaises(ValueError):
            derive_path(seed, "44'/60'/0'/0/0")


class TestBip32ReferenceVectors(unittest.TestCase):
    """Canonical BIP-32 test vectors from
    https://github.com/bitcoin/bips/blob/master/bip-0032.mediawiki."""

    BIP32_TV1_SEED = bytes.fromhex("000102030405060708090a0b0c0d0e0f")

    def test_vector1_m_0H(self):
        priv = derive_path(self.BIP32_TV1_SEED, "m/0'")
        self.assertEqual(
            priv.hex(),
            "edb2e14f9ee77d26dd93b4ecede8d16ed408ce149b6cd80b0715a2d911a0afea",
        )

    def test_vector1_m_0H_1(self):
        priv = derive_path(self.BIP32_TV1_SEED, "m/0'/1")
        self.assertEqual(
            priv.hex(),
            "3c6cb8d0f6a264c91ea8b5030fadaa8e538b020f0a387421a12de9319dc93368",
        )


if __name__ == "__main__":
    unittest.main()

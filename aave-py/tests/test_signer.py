"""Tests for the signer backends and the expected-address guard."""

import unittest

from lib.eth import build_eip1559_tx
from lib.signer import (
    MnemonicSigner,
    SignerError,
    assert_expected_address,
    build_signer,
)

ABANDON = (
    "abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon abandon abandon about"
)
# Canonical address for ABANDON at m/44'/60'/0'/0/0.
ABANDON_ADDR = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"


class TestMnemonicSigner(unittest.TestCase):
    def test_derives_the_canonical_abandon_address(self):
        s = MnemonicSigner(ABANDON, "m/44'/60'/0'/0/0")
        self.assertEqual(s.address, ABANDON_ADDR)
        self.assertEqual(s.kind, "mnemonic")
        s.close()

    def test_different_index_gives_a_different_address(self):
        a = MnemonicSigner(ABANDON, "m/44'/60'/0'/0/0")
        b = MnemonicSigner(ABANDON, "m/44'/60'/0'/0/1")
        self.assertNotEqual(a.address, b.address)
        a.close()
        b.close()

    def test_passphrase_changes_the_derived_address(self):
        """The whole reason a correct seed can derive an unexpected wallet."""
        plain = MnemonicSigner(ABANDON, "m/44'/60'/0'/0/0")
        with_pass = MnemonicSigner(ABANDON, "m/44'/60'/0'/0/0", passphrase="TREZOR")
        self.assertNotEqual(plain.address, with_pass.address)
        self.assertFalse(plain.uses_passphrase)
        self.assertTrue(with_pass.uses_passphrase)
        plain.close()
        with_pass.close()

    def test_describe_mentions_path_and_passphrase(self):
        s = MnemonicSigner(ABANDON, "m/44'/60'/0'/0/3", passphrase="x")
        self.assertIn("m/44'/60'/0'/0/3", s.describe())
        self.assertIn("passphrase", s.describe())
        s.close()

    def test_signs_a_transaction(self):
        s = MnemonicSigner(ABANDON, "m/44'/60'/0'/0/0")
        tx = build_eip1559_tx(
            chain_id=1, nonce=0, max_priority_fee_per_gas=10 ** 9,
            max_fee_per_gas=2 * 10 ** 9, gas_limit=21000,
            to="0x" + "11" * 20, value=0, data=b"",
        )
        raw = s.sign(tx)
        self.assertEqual(raw[0], 0x02)
        self.assertGreater(len(raw), 32)
        s.close()

    def test_signing_after_close_raises(self):
        s = MnemonicSigner(ABANDON, "m/44'/60'/0'/0/0")
        s.close()
        tx = build_eip1559_tx(
            chain_id=1, nonce=0, max_priority_fee_per_gas=1, max_fee_per_gas=2,
            gas_limit=21000, to="0x" + "11" * 20, value=0, data=b"",
        )
        with self.assertRaises(SignerError):
            s.sign(tx)

    def test_bad_word_count_raises_signer_error(self):
        with self.assertRaises(SignerError):
            MnemonicSigner("not a real mnemonic", "m/44'/60'/0'/0/0")

    def test_checksum_can_be_skipped(self):
        """Some wallets emit phrases that fail the BIP-39 checksum."""
        bad = " ".join(["abandon"] * 12)  # valid words, wrong checksum
        with self.assertRaises(SignerError):
            MnemonicSigner(bad, "m/44'/60'/0'/0/0")
        s = MnemonicSigner(bad, "m/44'/60'/0'/0/0", skip_checksum=True)
        self.assertTrue(s.address.startswith("0x"))
        s.close()


class TestBuildSigner(unittest.TestCase):
    def test_builds_a_mnemonic_signer(self):
        s = build_signer(kind="mnemonic", hd_path="m/44'/60'/0'/0/0", mnemonic=ABANDON)
        self.assertEqual(s.address, ABANDON_ADDR)
        s.close()

    def test_mnemonic_kind_without_a_mnemonic_raises(self):
        with self.assertRaises(SignerError):
            build_signer(kind="mnemonic", hd_path="m/44'/60'/0'/0/0", mnemonic=None)

    def test_unknown_kind_raises(self):
        with self.assertRaises(SignerError):
            build_signer(kind="trezor", hd_path="m/44'/60'/0'/0/0")


class TestExpectedAddressGuard(unittest.TestCase):
    def setUp(self):
        self.signer = MnemonicSigner(ABANDON, "m/44'/60'/0'/0/0")

    def tearDown(self):
        self.signer.close()

    def test_none_expected_is_a_no_op(self):
        assert_expected_address(self.signer, None)
        assert_expected_address(self.signer, "")

    def test_matching_address_passes_regardless_of_case(self):
        assert_expected_address(self.signer, ABANDON_ADDR.lower())
        assert_expected_address(self.signer, ABANDON_ADDR.upper().replace("0X", "0x"))
        assert_expected_address(self.signer, f"  {ABANDON_ADDR}  ")

    def test_mismatch_raises_and_names_both_addresses(self):
        other = "0x" + "22" * 20
        with self.assertRaises(SignerError) as cm:
            assert_expected_address(self.signer, other)
        msg = str(cm.exception)
        self.assertIn(self.signer.address, msg)
        self.assertIn("passphrase", msg)

    def test_guard_catches_a_missing_passphrase(self):
        """Right seed, right path, forgotten passphrase - must not sign."""
        expected = MnemonicSigner(
            ABANDON, "m/44'/60'/0'/0/0", passphrase="secret"
        )
        try:
            with self.assertRaises(SignerError):
                assert_expected_address(self.signer, expected.address)
        finally:
            expected.close()


if __name__ == "__main__":
    unittest.main()

"""Signing backends.

Two implementations, one interface:

  MnemonicSigner - derives a key in-process from a BIP-39 phrase (+ optional
                   passphrase) and signs with coincurve. Fast, unattended,
                   and only as safe as the machine it runs on.
  LedgerSigner   - delegates signing to a Ledger hardware wallet over USB.
                   The key never leaves the device; every transaction needs a
                   physical button press.

`aave.py` only ever touches `.address` and `.sign(tx)`, so a subcommand does
not need to know which backend it is driving.
"""

from __future__ import annotations

from lib.bip39_32 import derive_path, mnemonic_to_seed
from lib.crypto import derive_address
from lib.eth import Eip1559Tx, sign_eip1559_tx, to_checksum_address


class SignerError(Exception):
    """Raised for signer construction / signing failures that are the user's to fix."""


class MnemonicSigner:
    """Holds a derived private key in process memory for the duration of a run."""

    kind = "mnemonic"

    def __init__(
        self,
        mnemonic: str,
        hd_path: str,
        *,
        passphrase: str = "",
        skip_checksum: bool = False,
    ) -> None:
        try:
            seed = mnemonic_to_seed(
                mnemonic, passphrase=passphrase, skip_checksum=skip_checksum
            )
            self._priv = derive_path(seed, hd_path)
        except ValueError as e:
            raise SignerError(f"mnemonic / HD path error: {e}") from e
        finally:
            seed = None  # noqa: F841 - drop the reference promptly
        self.hd_path = hd_path
        self.address = derive_address(self._priv)
        self.uses_passphrase = bool(passphrase)

    def sign(self, tx: Eip1559Tx) -> bytes:
        if self._priv is None:
            raise SignerError("signer already closed")
        return sign_eip1559_tx(tx, self._priv)

    def close(self) -> None:
        """Best-effort wipe. CPython gives no guarantee, but drop the reference."""
        self._priv = None

    def describe(self) -> str:
        suffix = " + passphrase" if self.uses_passphrase else ""
        return f"mnemonic ({self.hd_path}{suffix})"


class LedgerSigner:
    """Signs on a Ledger hardware wallet. Requires the Ethereum app to be open.

    Construction opens the USB transport and reads the address at `hd_path`,
    so failures surface before any transaction is built.
    """

    kind = "ledger"

    def __init__(self, hd_path: str) -> None:
        from lib import ledger  # imported lazily: hidapi is an optional dependency

        self._ledger = ledger
        self.hd_path = hd_path
        try:
            self._device = ledger.open_device()
            self.address = to_checksum_address(
                ledger.get_address(self._device, hd_path)
            )
        except ledger.LedgerError as e:
            raise SignerError(str(e)) from e

    def sign(self, tx: Eip1559Tx) -> bytes:
        try:
            return self._ledger.sign_eip1559(
                self._device, self.hd_path, tx, expected_address=self.address
            )
        except self._ledger.LedgerError as e:
            raise SignerError(str(e)) from e

    def close(self) -> None:
        try:
            self._device.close()
        except Exception:
            pass

    def describe(self) -> str:
        return f"Ledger hardware wallet ({self.hd_path})"


def build_signer(
    *,
    kind: str,
    hd_path: str,
    mnemonic: str | None = None,
    passphrase: str = "",
    skip_checksum: bool = False,
) -> MnemonicSigner | LedgerSigner:
    if kind == "ledger":
        return LedgerSigner(hd_path)
    if kind == "mnemonic":
        if not mnemonic:
            raise SignerError("mnemonic signer selected but no mnemonic provided")
        return MnemonicSigner(
            mnemonic, hd_path, passphrase=passphrase, skip_checksum=skip_checksum
        )
    raise SignerError(f"unknown signer kind: {kind!r}")


def assert_expected_address(signer, expected: str | None) -> None:
    """Abort if the signer's address is not the one the operator expected.

    This is the cheapest possible guard against a wrong HD path, a missing
    BIP-39 passphrase, or the wrong mnemonic env var - all of which otherwise
    produce a perfectly valid signature from a wallet you did not mean to use.
    """
    if not expected:
        return
    if signer.address.lower() != expected.strip().lower():
        raise SignerError(
            "derived address does not match the expected address.\n"
            f"    expected: {to_checksum_address(expected.strip())}\n"
            f"    derived:  {signer.address}\n"
            "  Check HD_PATH, the mnemonic env var, and whether this seed needs "
            "a BIP-39 passphrase."
        )

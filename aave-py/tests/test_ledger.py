"""Tests for the hand-rolled Ledger APDU / HID layer.

Every pure function is covered here. The device round-trip itself cannot be
tested without hardware attached, so `TestFakeDeviceRoundTrip` drives the
transport against a scripted fake that speaks the same framing, which at least
pins the request bytes and the response parsing.
"""

import unittest

from lib import ledger
from lib.eth import build_eip1559_tx


class TestEncodeBip32Path(unittest.TestCase):
    def test_standard_metamask_path(self):
        # m/44'/60'/0'/0/0 -> 5 components, first three hardened.
        got = ledger.encode_bip32_path("m/44'/60'/0'/0/0")
        self.assertEqual(
            got.hex(),
            "05" "8000002c" "8000003c" "80000000" "00000000" "00000000",
        )

    def test_accepts_path_without_m_prefix(self):
        # compound-sniper's LEDGER_PATH is written without the leading "m/".
        self.assertEqual(
            ledger.encode_bip32_path("44'/60'/0'/0/0"),
            ledger.encode_bip32_path("m/44'/60'/0'/0/0"),
        )

    def test_h_and_uppercase_h_mean_hardened(self):
        self.assertEqual(
            ledger.encode_bip32_path("m/44h/60H/0'/0/0"),
            ledger.encode_bip32_path("m/44'/60'/0'/0/0"),
        )

    def test_nonzero_account_index(self):
        got = ledger.encode_bip32_path("m/44'/60'/3'/0/7")
        self.assertEqual(got[0], 5)
        self.assertEqual(got[9:13].hex(), "80000003")   # account 3, hardened
        self.assertEqual(got[17:21].hex(), "00000007")  # address index 7

    def test_rejects_empty_and_too_deep(self):
        with self.assertRaises(ledger.LedgerError):
            ledger.encode_bip32_path("m/")
        with self.assertRaises(ledger.LedgerError):
            ledger.encode_bip32_path("m/" + "/".join("0" for _ in range(11)))

    def test_rejects_non_numeric_component(self):
        with self.assertRaises(ledger.LedgerError):
            ledger.encode_bip32_path("m/44'/60'/x/0/0")

    def test_rejects_out_of_range_component(self):
        with self.assertRaises(ledger.LedgerError):
            ledger.encode_bip32_path(f"m/{0x80000000}")


class TestHidFraming(unittest.TestCase):
    def test_short_apdu_is_one_padded_packet(self):
        apdu = bytes([0xE0, 0x02, 0x00, 0x00, 0x01, 0xFF])
        packets = ledger.frame_apdu(apdu)
        self.assertEqual(len(packets), 1)
        self.assertEqual(len(packets[0]), 64)
        self.assertEqual(packets[0][0:2].hex(), "0101")  # channel
        self.assertEqual(packets[0][2], 0x05)            # tag
        self.assertEqual(packets[0][3:5].hex(), "0000")  # seq 0
        self.assertEqual(packets[0][5:7].hex(), "0006")  # total apdu length
        self.assertEqual(packets[0][7:13], apdu)
        self.assertEqual(packets[0][13:], b"\x00" * 51)  # zero padded

    def test_long_apdu_splits_and_increments_sequence(self):
        apdu = bytes(range(256)) * 2  # 512 bytes
        packets = ledger.frame_apdu(apdu)
        self.assertGreater(len(packets), 1)
        for i, p in enumerate(packets):
            self.assertEqual(len(p), 64)
            self.assertEqual(int.from_bytes(p[3:5], "big"), i)

    def test_framing_round_trips_through_deframe(self):
        for size in (1, 56, 57, 58, 100, 255, 512):
            apdu = bytes((i * 7) % 251 for i in range(size))
            with self.subTest(size=size):
                self.assertEqual(ledger.deframe_packets(ledger.frame_apdu(apdu)), apdu)

    def test_deframe_rejects_wrong_channel(self):
        packets = ledger.frame_apdu(b"\x01\x02")
        bad = bytearray(packets[0])
        bad[0:2] = (0x0202).to_bytes(2, "big")
        with self.assertRaises(ledger.LedgerError):
            ledger.deframe_packets([bytes(bad)])

    def test_deframe_rejects_wrong_tag(self):
        packets = ledger.frame_apdu(b"\x01\x02")
        bad = bytearray(packets[0])
        bad[2] = 0x06
        with self.assertRaises(ledger.LedgerError):
            ledger.deframe_packets([bytes(bad)])

    def test_deframe_rejects_out_of_order_sequence(self):
        packets = ledger.frame_apdu(bytes(200))
        with self.assertRaises(ledger.LedgerError):
            ledger.deframe_packets(list(reversed(packets)))

    def test_deframe_rejects_truncated_body(self):
        packets = ledger.frame_apdu(bytes(200))
        with self.assertRaises(ledger.LedgerError):
            ledger.deframe_packets(packets[:1])


class TestStatusWord(unittest.TestCase):
    def test_ok_strips_status_word(self):
        self.assertEqual(ledger.check_status(b"\xaa\xbb\x90\x00"), b"\xaa\xbb")

    def test_empty_payload_with_ok_is_allowed(self):
        self.assertEqual(ledger.check_status(b"\x90\x00"), b"")

    def test_user_rejection_is_explained(self):
        with self.assertRaises(ledger.LedgerError) as cm:
            ledger.check_status(b"\x69\x85")
        self.assertIn("Reject", str(cm.exception))

    def test_blind_signing_hint_for_6a80(self):
        with self.assertRaises(ledger.LedgerError) as cm:
            ledger.check_status(b"\x6a\x80")
        self.assertIn("Blind signing", str(cm.exception))

    def test_wrong_app_hint_for_6d00(self):
        with self.assertRaises(ledger.LedgerError) as cm:
            ledger.check_status(b"\x6d\x00")
        self.assertIn("Ethereum app", str(cm.exception))

    def test_unknown_status_still_raises_with_code(self):
        with self.assertRaises(ledger.LedgerError) as cm:
            ledger.check_status(b"\x12\x34")
        self.assertIn("0x1234", str(cm.exception))


class TestParseResponses(unittest.TestCase):
    def _address_response(self, addr_hex40: str) -> bytes:
        pubkey = bytes([0x04]) + bytes(64)
        return (
            bytes([len(pubkey)]) + pubkey
            + bytes([40]) + addr_hex40.encode("ascii")
        )

    def test_parse_address(self):
        addr = "60acc64ea33ecd4442887580e9c3b861dd141455"
        self.assertEqual(
            ledger.parse_get_address_response(self._address_response(addr)),
            "0x" + addr,
        )

    def test_parse_address_rejects_wrong_length(self):
        pubkey = bytes([0x04]) + bytes(64)
        payload = bytes([len(pubkey)]) + pubkey + bytes([4]) + b"dead"
        with self.assertRaises(ledger.LedgerError):
            ledger.parse_get_address_response(payload)

    def test_parse_address_rejects_truncated(self):
        with self.assertRaises(ledger.LedgerError):
            ledger.parse_get_address_response(b"\x41")

    def test_parse_signature(self):
        r_int = 0x1122334455667788112233445566778811223344556677881122334455667788
        s_int = 0x99AABBCC99AABBCC99AABBCC99AABBCC99AABBCC99AABBCC99AABBCC99AABBCC
        payload = bytes([1]) + r_int.to_bytes(32, "big") + s_int.to_bytes(32, "big")
        r, s, y = ledger.parse_signature_response(payload)
        self.assertEqual((r, s, y), (r_int, s_int, 1))

    def test_parse_signature_masks_v_to_parity(self):
        # Legacy-style v values must reduce to a 0/1 yParity for type-2 txs.
        for v, expected in ((0, 0), (1, 1), (27, 1), (28, 0), (37, 1), (38, 0)):
            payload = bytes([v]) + bytes(64)
            with self.subTest(v=v):
                self.assertEqual(ledger.parse_signature_response(payload)[2], expected)

    def test_parse_signature_rejects_wrong_length(self):
        with self.assertRaises(ledger.LedgerError):
            ledger.parse_signature_response(bytes(64))


class TestBuildSignApdus(unittest.TestCase):
    def test_single_chunk_carries_path_and_payload(self):
        apdus = ledger.build_sign_apdus("m/44'/60'/0'/0/0", b"\x02\xaa\xbb")
        self.assertEqual(len(apdus), 1)
        a = apdus[0]
        self.assertEqual(a[0], ledger.CLA)
        self.assertEqual(a[1], ledger.INS_SIGN_TX)
        self.assertEqual(a[2], ledger.P1_FIRST_CHUNK)
        self.assertEqual(a[4], len(a) - 5)  # LC matches actual data length
        self.assertEqual(a[5:26], ledger.encode_bip32_path("m/44'/60'/0'/0/0"))
        self.assertEqual(a[26:], b"\x02\xaa\xbb")

    def test_large_payload_chunks_with_continuation_p1(self):
        payload = bytes(700)
        apdus = ledger.build_sign_apdus("m/44'/60'/0'/0/0", payload)
        self.assertGreater(len(apdus), 1)
        self.assertEqual(apdus[0][2], ledger.P1_FIRST_CHUNK)
        for a in apdus[1:]:
            self.assertEqual(a[2], ledger.P1_MORE_CHUNKS)
        for a in apdus:
            self.assertLessEqual(a[4], ledger.MAX_APDU_DATA)
            self.assertEqual(a[4], len(a) - 5)

    def test_all_payload_bytes_are_transmitted_exactly_once(self):
        payload = bytes((i * 3) % 257 % 256 for i in range(1000))
        path = "m/44'/60'/0'/0/0"
        apdus = ledger.build_sign_apdus(path, payload)
        path_len = len(ledger.encode_bip32_path(path))
        rebuilt = apdus[0][5 + path_len:]
        for a in apdus[1:]:
            rebuilt += a[5:]
        self.assertEqual(rebuilt, payload)


class _FakeHid:
    """Scripted HID endpoint that frames a canned APDU response."""

    def __init__(self, response_apdu: bytes):
        self._outbound = ledger.frame_apdu(response_apdu)
        self.written: list[bytes] = []
        self.closed = False

    def write(self, packet):
        self.written.append(bytes(packet))

    def read(self, size, timeout_ms=0):
        if not self._outbound:
            return []
        return list(self._outbound.pop(0))

    def close(self):
        self.closed = True


class TestFakeDeviceRoundTrip(unittest.TestCase):
    def test_get_address_exchanges_expected_apdu(self):
        addr = "60acc64ea33ecd4442887580e9c3b861dd141455"
        pubkey = bytes([0x04]) + bytes(64)
        response = (
            bytes([len(pubkey)]) + pubkey + bytes([40]) + addr.encode() + b"\x90\x00"
        )
        fake = _FakeHid(response)
        device = ledger.LedgerDevice(fake)

        got = ledger.get_address(device, "m/44'/60'/0'/0/0")
        self.assertEqual(got, "0x" + addr)

        sent = ledger.deframe_packets(fake.written)
        self.assertEqual(sent[0], ledger.CLA)
        self.assertEqual(sent[1], ledger.INS_GET_PUBLIC_KEY)
        self.assertEqual(sent[2], ledger.P1_NO_DISPLAY)

    def test_device_error_status_propagates(self):
        device = ledger.LedgerDevice(_FakeHid(b"\x69\x85"))
        with self.assertRaises(ledger.LedgerError) as cm:
            ledger.get_address(device, "m/44'/60'/0'/0/0")
        self.assertIn("Reject", str(cm.exception))

    def test_sign_rejects_signature_from_wrong_key(self):
        """The recovery guard must block a signature that isn't ours."""
        tx = build_eip1559_tx(
            chain_id=1, nonce=0, max_priority_fee_per_gas=1, max_fee_per_gas=2,
            gas_limit=21000, to="0x" + "11" * 20, value=0, data=b"",
        )
        # A syntactically valid but unrelated signature.
        bogus = bytes([0]) + (1).to_bytes(32, "big") + (2).to_bytes(32, "big")
        device = ledger.LedgerDevice(_FakeHid(bogus + b"\x90\x00"))
        with self.assertRaises(ledger.LedgerError):
            ledger.sign_eip1559(
                device, "m/44'/60'/0'/0/0", tx,
                expected_address="0x" + "22" * 20,
            )


if __name__ == "__main__":
    unittest.main()

"""Ledger hardware wallet transport, hand-rolled.

Speaks the Ledger Ethereum app's APDU protocol directly over USB HID so the
only new dependency is `hidapi` - a thin wrapper around the widely-deployed
hidapi C library. This deliberately avoids the @ledgerhq / ledgereth stacks,
which would pull in dozens of transitive packages and blow up the trust
surface this project is built around.

Protocol references:
  HID framing   - 64-byte reports: channel(2) | tag(0x05) | seq(2) | [len(2)] | data
  APDU          - CLA(0xE0) INS P1 P2 LC | data, response ends with a 2-byte status word
  GET PUBLIC KEY- INS 0x02
  SIGN TX       - INS 0x04, payload is the EIP-2718 envelope (0x02 || rlp(unsigned))

The device must be unlocked with the Ethereum app open. Because Aave V3 and
Curve are not in Ledger's clear-signing registry, "Blind signing" must be
enabled in the Ethereum app settings; the device will then show the function
selector and raw calldata, which should match what `verify` printed.
"""

from __future__ import annotations

from lib import rlp
from lib.crypto import recover_address
from lib.eth import Eip1559Tx, sighash_eip1559

LEDGER_VENDOR_ID = 0x2C97
HID_PACKET_SIZE = 64
HID_TAG_APDU = 0x05
DEFAULT_CHANNEL = 0x0101

CLA = 0xE0
INS_GET_PUBLIC_KEY = 0x02
INS_SIGN_TX = 0x04
P1_FIRST_CHUNK = 0x00
P1_MORE_CHUNKS = 0x80
P1_NO_DISPLAY = 0x00
P2_NO_CHAINCODE = 0x00

MAX_APDU_DATA = 255
STATUS_OK = 0x9000

# Status words worth translating into something actionable.
STATUS_MESSAGES = {
    0x6985: "rejected on device (you pressed Reject, or the tx timed out)",
    0x6A80: "device refused the data - enable 'Blind signing' in the Ethereum app settings",
    0x6B00: "wrong parameter - the Ethereum app may be too old for EIP-1559",
    0x6D00: "instruction not supported - is the Ethereum app open (not the dashboard)?",
    0x6E00: "class not supported - wrong app is open on the device",
    0x6F00: "device reported an internal error",
    0x5515: "device is locked - unlock it and reopen the Ethereum app",
    0x6804: "device is locked or busy",
}


class LedgerError(Exception):
    """Any failure talking to the device, phrased for the operator."""


# ---------------------------------------------------------------- HID framing

def frame_apdu(apdu: bytes, channel: int = DEFAULT_CHANNEL) -> list[bytes]:
    """Split an APDU into 64-byte HID reports.

    The first report carries a 2-byte big-endian total length; every report is
    zero-padded to exactly HID_PACKET_SIZE.
    """
    packets: list[bytes] = []
    payload = len(apdu).to_bytes(2, "big") + apdu
    offset = 0
    seq = 0
    while offset < len(payload):
        header = channel.to_bytes(2, "big") + bytes([HID_TAG_APDU]) + seq.to_bytes(2, "big")
        room = HID_PACKET_SIZE - len(header)
        chunk = payload[offset:offset + room]
        packets.append((header + chunk).ljust(HID_PACKET_SIZE, b"\x00"))
        offset += room
        seq += 1
    return packets


def deframe_packets(packets: list[bytes], channel: int = DEFAULT_CHANNEL) -> bytes:
    """Reassemble HID reports into the APDU response (status word included)."""
    if not packets:
        raise LedgerError("no response from device")
    body = b""
    expected_len: int | None = None
    for seq, packet in enumerate(packets):
        if len(packet) < 5:
            raise LedgerError(f"short HID report: {len(packet)} bytes")
        got_channel = int.from_bytes(packet[0:2], "big")
        if got_channel != channel:
            raise LedgerError(f"unexpected HID channel 0x{got_channel:04x}")
        if packet[2] != HID_TAG_APDU:
            raise LedgerError(f"unexpected HID tag 0x{packet[2]:02x}")
        got_seq = int.from_bytes(packet[3:5], "big")
        if got_seq != seq:
            raise LedgerError(f"out-of-order HID report: expected seq {seq}, got {got_seq}")
        if seq == 0:
            if len(packet) < 7:
                raise LedgerError("first HID report too short to contain a length")
            expected_len = int.from_bytes(packet[5:7], "big")
            body += packet[7:]
        else:
            body += packet[5:]
        if expected_len is not None and len(body) >= expected_len:
            break
    if expected_len is None:
        raise LedgerError("device response missing length header")
    if len(body) < expected_len:
        raise LedgerError(
            f"truncated device response: expected {expected_len} bytes, got {len(body)}"
        )
    return body[:expected_len]


def check_status(response: bytes) -> bytes:
    """Split off and validate the trailing 2-byte status word."""
    if len(response) < 2:
        raise LedgerError(f"response too short for a status word: {response.hex()}")
    payload, sw = response[:-2], int.from_bytes(response[-2:], "big")
    if sw == STATUS_OK:
        return payload
    hint = STATUS_MESSAGES.get(sw)
    if hint:
        raise LedgerError(f"device error 0x{sw:04x}: {hint}")
    raise LedgerError(f"device error 0x{sw:04x}")


# ------------------------------------------------------------- path encoding

def encode_bip32_path(path: str) -> bytes:
    """Encode "m/44'/60'/0'/0/0" as count byte + 4-byte big-endian components."""
    cleaned = path.strip()
    if cleaned.startswith("m/"):
        cleaned = cleaned[2:]
    elif cleaned.startswith("m"):
        cleaned = cleaned[1:]
    parts = [p for p in cleaned.split("/") if p]
    if not parts:
        raise LedgerError(f"empty derivation path: {path!r}")
    if len(parts) > 10:
        raise LedgerError(f"derivation path too deep ({len(parts)} levels): {path!r}")
    out = bytes([len(parts)])
    for part in parts:
        hardened = part.endswith(("'", "h", "H"))
        raw = part[:-1] if hardened else part
        try:
            index = int(raw)
        except ValueError as e:
            raise LedgerError(f"bad path component {part!r} in {path!r}") from e
        if index < 0 or index >= 0x80000000:
            raise LedgerError(f"path component out of range: {part!r}")
        if hardened:
            index |= 0x80000000
        out += index.to_bytes(4, "big")
    return out


def build_sign_apdus(path: str, payload: bytes) -> list[bytes]:
    """Chunk an EIP-2718 payload into SIGN TX APDUs.

    The first APDU carries the derivation path; the device streams the rest.
    """
    path_bytes = encode_bip32_path(path)
    apdus: list[bytes] = []
    first_room = MAX_APDU_DATA - len(path_bytes)
    if first_room <= 0:
        raise LedgerError("derivation path leaves no room for payload in the first APDU")
    data = path_bytes + payload[:first_room]
    apdus.append(bytes([CLA, INS_SIGN_TX, P1_FIRST_CHUNK, 0x00, len(data)]) + data)
    offset = first_room
    while offset < len(payload):
        chunk = payload[offset:offset + MAX_APDU_DATA]
        apdus.append(bytes([CLA, INS_SIGN_TX, P1_MORE_CHUNKS, 0x00, len(chunk)]) + chunk)
        offset += len(chunk)
    return apdus


def parse_get_address_response(payload: bytes) -> str:
    """Extract the 0x-prefixed address from a GET PUBLIC KEY response."""
    if len(payload) < 2:
        raise LedgerError("GET PUBLIC KEY response too short")
    pubkey_len = payload[0]
    cursor = 1 + pubkey_len
    if len(payload) < cursor + 1:
        raise LedgerError("GET PUBLIC KEY response truncated before address")
    addr_len = payload[cursor]
    cursor += 1
    ascii_addr = payload[cursor:cursor + addr_len]
    if len(ascii_addr) != addr_len:
        raise LedgerError("GET PUBLIC KEY address field truncated")
    try:
        text = ascii_addr.decode("ascii")
    except UnicodeDecodeError as e:
        raise LedgerError("device returned a non-ASCII address") from e
    if len(text) != 40:
        raise LedgerError(f"device returned a {len(text)}-char address, expected 40")
    return "0x" + text


def parse_signature_response(payload: bytes) -> tuple[int, int, int]:
    """Return (r, s, y_parity) from a SIGN TX response of v(1) | r(32) | s(32)."""
    if len(payload) != 65:
        raise LedgerError(f"signature response must be 65 bytes, got {len(payload)}")
    v = payload[0]
    r = int.from_bytes(payload[1:33], "big")
    s = int.from_bytes(payload[33:65], "big")
    # For EIP-1559 the app returns the recovery id; only its low bit is the parity.
    return r, s, v & 1


# ------------------------------------------------------------------ transport

class LedgerDevice:
    """Thin wrapper over an open hid handle that exchanges whole APDUs."""

    def __init__(self, handle, channel: int = DEFAULT_CHANNEL) -> None:
        self._handle = handle
        self._channel = channel

    def exchange(self, apdu: bytes, timeout_ms: int = 60_000) -> bytes:
        for packet in frame_apdu(apdu, self._channel):
            self._handle.write(packet)
        packets: list[bytes] = []
        expected_len: int | None = None
        received = 0
        while True:
            report = self._handle.read(HID_PACKET_SIZE, timeout_ms)
            if not report:
                raise LedgerError(
                    "timed out waiting for the device "
                    "(is it unlocked with the Ethereum app open?)"
                )
            report = bytes(report)
            packets.append(report)
            if expected_len is None:
                if len(report) < 7:
                    raise LedgerError("first HID report too short")
                expected_len = int.from_bytes(report[5:7], "big")
                received = len(report) - 7
            else:
                received += len(report) - 5
            if received >= expected_len:
                break
        return check_status(deframe_packets(packets, self._channel))

    def close(self) -> None:
        self._handle.close()


def open_device() -> LedgerDevice:
    try:
        import hid  # noqa: PLC0415 - optional dependency, imported on demand
    except ImportError as e:
        raise LedgerError(
            "Ledger support needs the 'hidapi' package.\n"
            "  Install it with:  .venv/bin/pip install -r requirements-ledger.txt"
        ) from e

    candidates = [d for d in hid.enumerate() if d["vendor_id"] == LEDGER_VENDOR_ID]
    if not candidates:
        raise LedgerError(
            "no Ledger device found over USB.\n"
            "  Plug it in, unlock it, and open the Ethereum app."
        )
    # Ledger exposes several HID interfaces; the APDU one advertises usage page 0xffa0.
    candidates.sort(key=lambda d: 0 if d.get("usage_page") == 0xFFA0 else 1)
    chosen = candidates[0]
    handle = hid.device()
    try:
        if chosen.get("path"):
            handle.open_path(chosen["path"])
        else:
            handle.open(chosen["vendor_id"], chosen["product_id"])
    except OSError as e:
        raise LedgerError(
            f"could not open the Ledger HID interface: {e}\n"
            "  Close Ledger Live (it holds an exclusive claim on the device) and retry."
        ) from e
    handle.set_nonblocking(0)
    return LedgerDevice(handle)


def get_address(device: LedgerDevice, path: str, display: bool = False) -> str:
    path_bytes = encode_bip32_path(path)
    p1 = 0x01 if display else P1_NO_DISPLAY
    apdu = bytes([CLA, INS_GET_PUBLIC_KEY, p1, P2_NO_CHAINCODE, len(path_bytes)]) + path_bytes
    return parse_get_address_response(device.exchange(apdu))


def sign_eip1559(
    device: LedgerDevice,
    path: str,
    tx: Eip1559Tx,
    expected_address: str | None = None,
) -> bytes:
    """Stream an unsigned EIP-1559 tx to the device and return the signed raw bytes.

    If `expected_address` is given, the signature is recovered and checked
    against it, so a transaction signed by the wrong derivation can never reach
    the mempool.
    """
    unsigned_payload = [
        tx.chain_id,
        tx.nonce,
        tx.max_priority_fee_per_gas,
        tx.max_fee_per_gas,
        tx.gas_limit,
        bytes.fromhex(tx.to[2:] if tx.to.startswith("0x") else tx.to),
        tx.value,
        tx.data,
        tx.access_list,
    ]
    envelope = b"\x02" + rlp.encode(unsigned_payload)

    payload: bytes | None = None
    for apdu in build_sign_apdus(path, envelope):
        payload = device.exchange(apdu)
    if payload is None:
        raise LedgerError("device returned no signature")
    r, s, y_parity = parse_signature_response(payload)

    if expected_address is not None:
        recovered = recover_address(sighash_eip1559(tx), r, s, y_parity)
        if recovered.lower() != expected_address.lower():
            raise LedgerError(
                "signature does not recover to the expected address; refusing to broadcast.\n"
                f"    expected:  {expected_address}\n"
                f"    recovered: {recovered}"
            )

    signed = unsigned_payload + [y_parity, r, s]
    return b"\x02" + rlp.encode(signed)

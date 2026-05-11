"""EIP-55 checksum and EIP-1559 (type-2) transaction signing."""

from __future__ import annotations

from dataclasses import dataclass

from lib import abi, rlp
from lib.crypto import sign_recoverable
from lib.keccak import keccak256


def to_checksum_address(addr: str) -> str:
    """EIP-55 checksum encoding."""
    s = addr[2:] if addr.startswith(("0x", "0X")) else addr
    if len(s) != 40:
        raise ValueError(f"address must be 40 hex chars, got {len(s)}")
    s = s.lower()
    h = keccak256(s.encode("ascii")).hex()
    out = ["0x"]
    for i, c in enumerate(s):
        if c.isdigit():
            out.append(c)
        else:
            out.append(c.upper() if int(h[i], 16) >= 8 else c)
    return "".join(out)


@dataclass(frozen=True)
class Eip1559Tx:
    chain_id: int
    nonce: int
    max_priority_fee_per_gas: int
    max_fee_per_gas: int
    gas_limit: int
    to: str
    value: int
    data: bytes
    access_list: list  # always [] for our use case


def build_eip1559_tx(
    *,
    chain_id: int,
    nonce: int,
    max_priority_fee_per_gas: int,
    max_fee_per_gas: int,
    gas_limit: int,
    to: str,
    value: int,
    data: bytes,
    access_list: list | None = None,
) -> Eip1559Tx:
    return Eip1559Tx(
        chain_id=chain_id,
        nonce=nonce,
        max_priority_fee_per_gas=max_priority_fee_per_gas,
        max_fee_per_gas=max_fee_per_gas,
        gas_limit=gas_limit,
        to=to,
        value=value,
        data=data,
        access_list=access_list if access_list is not None else [],
    )


def _to_bytes(addr: str) -> bytes:
    s = addr[2:] if addr.startswith(("0x", "0X")) else addr
    return bytes.fromhex(s)


def _unsigned_payload(tx: Eip1559Tx) -> list:
    return [
        tx.chain_id,
        tx.nonce,
        tx.max_priority_fee_per_gas,
        tx.max_fee_per_gas,
        tx.gas_limit,
        _to_bytes(tx.to),
        tx.value,
        tx.data,
        tx.access_list,
    ]


def sighash_eip1559(tx: Eip1559Tx) -> bytes:
    return keccak256(b"\x02" + rlp.encode(_unsigned_payload(tx)))


def sign_eip1559_tx(tx: Eip1559Tx, priv: bytes) -> bytes:
    """Return the raw signed transaction bytes ready for eth_sendRawTransaction."""
    sighash = sighash_eip1559(tx)
    r, s, y = sign_recoverable(priv, sighash)
    payload = _unsigned_payload(tx) + [y, r, s]
    return b"\x02" + rlp.encode(payload)


# Re-export so callers don't have to import abi separately for this one helper.
encode_withdraw_call = abi.encode_withdraw_call

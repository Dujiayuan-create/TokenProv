"""Ed25519 signatures over domain-separated canonical objects."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from tokenprov.common.serialization import canonical_bytes


@dataclass(frozen=True)
class KeyPair:
    private_key: str
    public_key: str


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"), validate=True)


def generate_keypair() -> KeyPair:
    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                        serialization.NoEncryption())
    public_raw = private.public_key().public_bytes(serialization.Encoding.Raw,
                                                   serialization.PublicFormat.Raw)
    return KeyPair(_b64(private_raw), _b64(public_raw))


def _message(domain: str, value: object) -> bytes:
    return b"TOKENPROV-SIGNATURE\x00" + domain.encode() + b"\x00" + canonical_bytes(value)


def sign_object(private_key: str, domain: str, value: object) -> str:
    return _b64(Ed25519PrivateKey.from_private_bytes(_unb64(private_key)).sign(_message(domain, value)))


def verify_object(public_key: str, domain: str, value: object, signature: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(_unb64(public_key)).verify(_unb64(signature), _message(domain, value))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


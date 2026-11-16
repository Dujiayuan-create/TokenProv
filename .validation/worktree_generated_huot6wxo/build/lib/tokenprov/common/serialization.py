"""Deterministic, typed serialization and hashing."""
from __future__ import annotations

import dataclasses
import hashlib
import json
from enum import Enum
from typing import Any


def primitive(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {f.name: primitive(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise TypeError("canonical mappings require string keys")
        return {k: primitive(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [primitive(v) for v in value]
    if isinstance(value, bytes):
        return {"$bytes": value.hex()}
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported canonical type: {type(value)!r}")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(primitive(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(domain: str, value: Any) -> str:
    framed = b"TOKENPROV\x00" + domain.encode() + b"\x00" + canonical_bytes(value)
    return hashlib.sha256(framed).hexdigest()


def commitment(domain: str, value: Any, opening: str) -> str:
    return digest("commitment/" + domain, {"value": value, "opening": opening})


def verify_opening(domain: str, value: Any, opening: str, expected: str) -> bool:
    return commitment(domain, value, opening) == expected

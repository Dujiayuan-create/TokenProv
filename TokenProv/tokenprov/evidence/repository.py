"""Append-only local off-chain evidence repository."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from tokenprov.common.models import EvidenceObject
from tokenprov.common.serialization import canonical_bytes, digest


_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _safe(value: str, name: str) -> str:
    if not _COMPONENT.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"unsafe {name}")
    return value


class LocalEvidenceRepository:
    """Filesystem-backed V; content-addressed objects are never overwritten."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._sequence = sum(1 for _ in self.root.rglob("*.json"))

    def put(self, request_id: str, evidence_type: str, payload: Any, *,
            call_id: str | None = None, deployment_id: str | None = None) -> EvidenceObject:
        request_id = _safe(request_id, "request_id")
        evidence_type = _safe(evidence_type, "evidence_type")
        if call_id is not None: _safe(call_id, "call_id")
        if deployment_id is not None: _safe(deployment_id, "deployment_id")
        with self._lock:
            sequence = self._sequence
            self._sequence += 1
            obj = EvidenceObject(request_id, evidence_type, sequence, payload, call_id,
                                 deployment_id, time.time_ns())
            obj = replace(obj, content_hash=digest("evidence-object/v1", obj.hash_payload()))
            directory = self.root / request_id / evidence_type
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{obj.content_hash}.json"
            encoded = canonical_bytes(obj) + b"\n"
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError as exc:
                raise ValueError("evidence object already exists") from exc
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            return obj

    def get(self, request_id: str, evidence_type: str, content_hash: str) -> dict[str, Any]:
        path = self.root / _safe(request_id, "request_id") / _safe(evidence_type, "evidence_type") / f"{_safe(content_hash, 'content_hash')}.json"
        data = json.loads(path.read_bytes())
        expected = data.pop("content_hash")
        if expected != content_hash or digest("evidence-object/v1", data) != expected:
            raise ValueError("evidence content hash mismatch")
        data["content_hash"] = expected
        return data

    def request_bytes(self, request_id: str) -> int:
        directory = self.root / _safe(request_id, "request_id")
        return sum(path.stat().st_size for path in directory.rglob("*.json")) if directory.exists() else 0

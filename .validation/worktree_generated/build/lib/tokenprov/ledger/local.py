"""Durable local authenticated ledger for protocol experiments."""
from __future__ import annotations

import json
import os
import threading
from abc import ABC, abstractmethod
from dataclasses import replace
from pathlib import Path
from typing import Any

from tokenprov.common.models import LedgerEntry
from tokenprov.common.serialization import canonical_bytes, digest
from tokenprov.crypto.signing import sign_object, verify_object


class LedgerBackend(ABC):
    @abstractmethod
    def append(self, record_id: str, record_type: str, payload: Any,
               member_id: str, private_key: str) -> LedgerEntry: ...

    @abstractmethod
    def get(self, record_id: str) -> LedgerEntry | None: ...

    @abstractmethod
    def verify_membership(self, record_id: str) -> bool: ...

    @abstractmethod
    def order(self) -> tuple[str, ...]: ...

    @abstractmethod
    def wait_finality(self, record_id: str, timeout_s: float = 0.0) -> bool: ...


class LocalAuthenticatedLedger(LedgerBackend):
    """Hash-chained signed JSONL ledger with fsync finality on one host."""

    def __init__(self, path: str | Path, member_keys: dict[str, str]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.member_keys = dict(member_keys)
        self._lock = threading.Lock()
        self._entries = self._load()
        self._by_id = {entry.record_id: entry for entry in self._entries}
        self._writable = True

    def _load(self) -> list[LedgerEntry]:
        if not self.path.exists(): return []
        entries = [LedgerEntry(**row) for row in
                   (json.loads(line) for line in self.path.read_text().splitlines() if line)]
        if len({entry.record_id for entry in entries}) != len(entries):
            raise ValueError("duplicate ledger record_id")
        for index, entry in enumerate(entries):
            if entry.sequence != index or not self._verify_at(entries, index):
                raise ValueError("invalid authenticated ledger chain")
        return entries

    def _verify_at(self, entries: list[LedgerEntry], index: int) -> bool:
        entry = entries[index]
        previous = entries[index - 1].entry_hash if index else digest("ledger-genesis/v1", {})
        key = self.member_keys.get(entry.member_id)
        return bool(key and entry.previous_hash == previous
                    and entry.entry_hash == digest("ledger-entry/v1", entry.hash_payload())
                    and verify_object(key, "ledger-entry/v1", entry.signed_payload(), entry.signature))

    def append(self, record_id: str, record_type: str, payload: Any,
               member_id: str, private_key: str) -> LedgerEntry:
        return self.append_batch(((record_id, record_type, payload, member_id, private_key),))[0]

    def append_batch(self, records) -> tuple[LedgerEntry, ...]:
        """Keep every signed record, acknowledging one durable write per batch."""
        records = tuple(records)
        if not records: return ()
        with self._lock:
            if not self._writable:
                raise RuntimeError("ledger write failed; reopen and validate before further appends")
            ids = [record[0] for record in records]
            if len(set(ids)) != len(ids) or any(record_id in self._by_id for record_id in ids):
                raise ValueError("record_id is append-only and unique")
            previous = self._entries[-1].entry_hash if self._entries else digest("ledger-genesis/v1", {})
            pending = []
            for record_id, record_type, payload, member_id, private_key in records:
                if member_id not in self.member_keys: raise ValueError("member is not authorized")
                entry = LedgerEntry(len(self._entries) + len(pending), record_id, record_type, payload, member_id, previous)
                entry = replace(entry, entry_hash=digest("ledger-entry/v1", entry.hash_payload()))
                entry = replace(entry, signature=sign_object(private_key, "ledger-entry/v1", entry.signed_payload()))
                if not verify_object(self.member_keys[member_id], "ledger-entry/v1",
                                     entry.signed_payload(), entry.signature):
                    raise ValueError("private key does not match authenticated member")
                pending.append(entry)
                previous = entry.entry_hash
            encoded = b"".join(canonical_bytes(entry) + b"\n" for entry in pending)
            created = not self.path.exists()
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "ab") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                # The directory changes only when the ledger file is created.
                if created and os.name != "nt":
                    directory_fd = os.open(self.path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
            except OSError:
                # Shared stores must not append from a stale in-memory head
                # after an ambiguous partial write or failed durability ack.
                self._writable = False
                raise
            self._entries.extend(pending)
            self._by_id.update((entry.record_id, entry) for entry in pending)
            return tuple(pending)

    def get(self, record_id: str) -> LedgerEntry | None:
        return self._by_id.get(record_id)

    def verify_membership(self, record_id: str) -> bool:
        entry = self._by_id.get(record_id)
        return self._writable and entry is not None and self._verify_at(self._entries, entry.sequence)

    def order(self) -> tuple[str, ...]:
        return tuple(entry.record_id for entry in self._entries)

    def wait_finality(self, record_id: str, timeout_s: float = 0.0) -> bool:
        del timeout_s
        return self.verify_membership(record_id)

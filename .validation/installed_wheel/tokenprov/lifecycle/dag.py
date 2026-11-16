from __future__ import annotations
from collections import deque
from dataclasses import replace
from tokenprov.common.models import LifecycleEvent, TerminalState
from tokenprov.common.serialization import digest
from tokenprov.crypto.signing import sign_object, verify_object


class LifecycleError(ValueError): pass


class LifecycleDAG:
    def __init__(self, request_id: str, authorization_digest: str):
        self.request_id, self.authorization_digest = request_id, authorization_digest
        self.events: dict[str, LifecycleEvent] = {}
        self.hashes: dict[str, str] = {}
        self.call_ids: set[str] = set()
        self.terminal: dict[str, TerminalState] = {}

    def add(self, event: LifecycleEvent, issuer_private_key: str) -> LifecycleEvent:
        if event.request_id != self.request_id or event.authorization_digest != self.authorization_digest:
            raise LifecycleError("event belongs to another authorization")
        if event.event_id in self.events:
            raise LifecycleError("duplicate event_id")
        if any(p not in self.hashes for p in event.predecessor_hashes):
            raise LifecycleError("unknown or cross-request predecessor")
        if self.events and not event.predecessor_hashes:
            raise LifecycleError("non-root event is not authorization-reachable")
        if not self.events and event.event_type != "authorization_root":
            raise LifecycleError("first event must be unique authorization_root")
        if event.event_type == "call_created":
            if not event.call_id or event.call_id in self.call_ids:
                raise LifecycleError("call_id must be fresh")
            self.call_ids.add(event.call_id)
        if event.state in {s.value for s in TerminalState}:
            if not event.call_id or event.call_id not in self.call_ids:
                raise LifecycleError("terminal event has no generated call")
            if event.call_id in self.terminal:
                raise LifecycleError("call has multiple terminal states")
            self.terminal[event.call_id] = TerminalState(event.state)
        signed = replace(event, signature=sign_object(issuer_private_key, "lifecycle-event/v1", event.signed_payload()))
        event_hash = digest("lifecycle-event/v1", signed)
        self.events[event.event_id], self.hashes[event_hash] = signed, event.event_id
        return signed

    def validate(self, issuer_keys: dict[str, str], require_closed: bool = True) -> bool:
        roots = [e for e in self.events.values() if e.event_type == "authorization_root"]
        if len(roots) != 1 or roots[0].predecessor_hashes: return False
        if any(key != event.event_id for key, event in self.events.items()): return False
        by_hash = {digest("lifecycle-event/v1", event): event for event in self.events.values()}
        if len(by_hash) != len(self.events): return False
        if self.hashes != {event_hash: event.event_id for event_hash, event in by_hash.items()}: return False
        if any(event.request_id != self.request_id or event.authorization_digest != self.authorization_digest
               for event in self.events.values()): return False
        if any(p not in by_hash for event in self.events.values() for p in event.predecessor_hashes): return False

        root_hash = digest("lifecycle-event/v1", roots[0])
        # Kahn traversal avoids Python's recursion limit on long agent workflows.
        pending = {key: len(event.predecessor_hashes) for key, event in by_hash.items()}
        children = {key: [] for key in by_hash}
        for key, event in by_hash.items():
            if key != root_hash and not event.predecessor_hashes: return False
            if len(set(event.predecessor_hashes)) != len(event.predecessor_hashes): return False
            for predecessor in event.predecessor_hashes:
                children[predecessor].append(key)
        ready = deque([root_hash])
        visited = 0
        while ready:
            current = ready.popleft()
            visited += 1
            for child in children[current]:
                pending[child] -= 1
                if pending[child] == 0: ready.append(child)
        if visited != len(by_hash): return False

        terminals: set[str] = set()
        created: set[str] = set()
        for event in self.events.values():
            if event.issuer not in issuer_keys or not verify_object(issuer_keys[event.issuer], "lifecycle-event/v1", event.signed_payload(), event.signature): return False
            if event.event_type == "call_created":
                if not event.call_id or event.call_id in created: return False
                created.add(event.call_id)
        for event in self.events.values():
            if event.state in {s.value for s in TerminalState}:
                if event.call_id not in created or event.call_id in terminals: return False
                terminals.add(event.call_id)
        return not require_closed or terminals == created

    def root(self) -> str:
        return digest("lifecycle-dag-root/v1", sorted(self.hashes))

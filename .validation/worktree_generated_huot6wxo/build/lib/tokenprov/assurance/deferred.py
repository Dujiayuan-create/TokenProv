"""Pre-settlement witness fixation for deferred proving.

This module verifies commitment binding only; it is not a ZK prover.
"""
from __future__ import annotations

from tokenprov.common.models import WitnessCommitment
from tokenprov.common.serialization import commitment, verify_opening


class DeferredWitnessRegistry:
    def __init__(self):
        self._fixed: dict[tuple[str, str], WitnessCommitment] = {}

    def fix(self, request_id: str, call_id: str, deployment_id: str,
            witness: object, opening: str) -> WitnessCommitment:
        key = (request_id, call_id)
        fixed = WitnessCommitment(request_id, call_id, deployment_id,
                                  commitment("provider-witness/v1", witness, opening))
        existing = self._fixed.get(key)
        if existing is not None and existing != fixed:
            raise ValueError("witness commitment is immutable after fixation")
        self._fixed[key] = fixed
        return fixed

    def get(self, request_id: str, call_id: str) -> WitnessCommitment | None:
        return self._fixed.get((request_id, call_id))

    def verify_opening(self, fixed: WitnessCommitment, witness: object, opening: str) -> bool:
        current = self.get(fixed.request_id, fixed.call_id)
        return current == fixed and verify_opening("provider-witness/v1", witness, opening,
                                                   fixed.commitment)

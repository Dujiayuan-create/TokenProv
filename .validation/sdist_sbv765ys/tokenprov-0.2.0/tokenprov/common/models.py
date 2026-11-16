"""Protocol records. Signed payload methods exclude their signature fields."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TerminalState(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABORTED = "aborted"


@dataclass(frozen=True)
class Authorization:
    request_id: str
    request_context: dict[str, Any]
    input_commitment: str
    policy: dict[str, Any]
    nonce: str
    user_id: str
    user_signature: str = ""
    def signed_payload(self):
        return {k: v for k, v in self.__dict__.items() if k != "user_signature"}


@dataclass(frozen=True)
class LifecycleEvent:
    request_id: str
    authorization_digest: str
    event_id: str
    call_id: str | None
    event_type: str
    predecessor_hashes: tuple[str, ...]
    payload_commitment: str
    state: str
    issuer: str
    signature: str = ""
    def signed_payload(self):
        return {k: v for k, v in self.__dict__.items() if k != "signature"}


@dataclass(frozen=True)
class Handoff:
    request_id: str
    call_id: str
    direction: str
    boundary_commitment: str
    causal_references: tuple[str, ...]
    sender: str
    receiver: str
    sender_signature: str = ""
    receiver_signature: str = ""
    def context(self):
        return {k: v for k, v in self.__dict__.items() if k not in {"sender_signature", "receiver_signature"}}


@dataclass(frozen=True)
class DeploymentDescriptor:
    model_id: str
    model_version: str
    model_hash: str
    tokenizer_hash: str
    canonicalization_hash: str
    serving_semantics_hash: str


@dataclass(frozen=True)
class DeploymentRegistration:
    deployment_id: str
    provider_id: str
    descriptor: DeploymentDescriptor
    deployment_commitment: str
    signature: str = ""
    def signed_payload(self):
        return {k: v for k, v in self.__dict__.items() if k != "signature"}


@dataclass(frozen=True)
class UsageVector:
    n_pf: int = 0
    n_cr: int = 0
    n_cw: int = 0
    n_rea: int = 0
    n_out: int = 0
    def __post_init__(self):
        if any(type(v) is not int or v < 0 for v in self.__dict__.values()):
            raise ValueError("usage counters must be non-negative integers")


@dataclass(frozen=True)
class PricePolicy:
    """Integer-only price schedule; every rate is in micro-units per token."""
    version: str
    n_pf_rate: int = 0
    n_cr_rate: int = 0
    n_cw_rate: int = 0
    n_rea_rate: int = 0
    n_out_rate: int = 0

    def __post_init__(self):
        if not self.version or any(type(v) is not int or v < 0
                                   for k, v in self.__dict__.items() if k != "version"):
            raise ValueError("price version is required and rates must be non-negative integers")

    def charge(self, usage: UsageVector) -> int:
        return sum(getattr(usage, name) * getattr(self, name + "_rate")
                   for name in ("n_pf", "n_cr", "n_cw", "n_rea", "n_out"))


@dataclass(frozen=True)
class CallIntent:
    request_id: str
    call_id: str
    structured_messages: tuple[dict[str, Any], ...]
    inherited_constraints: dict[str, Any]


@dataclass(frozen=True)
class ServiceConfiguration:
    provider_id: str
    model_id: str
    endpoint: str | None
    decoding_config: dict[str, Any]
    cache_policy: dict[str, Any]
    metering_policy: dict[str, Any]
    price_version: str


@dataclass(frozen=True)
class ProviderCallRequest:
    request_id: str
    call_id: str
    transformed_messages: tuple[dict[str, Any], ...]
    configuration: ServiceConfiguration
    transformation_rule: dict[str, Any]
    hb_handoff_hash: str


@dataclass(frozen=True)
class ProviderCallEvidence:
    """Shared provider relation values used to enforce R_link."""
    request_id: str
    call_id: str
    authorization_digest: str
    deployment_id: str
    service_config_hash: str
    hb_handoff_hash: str
    bp_handoff_hash: str
    transformation_hash: str
    input_hash: str
    cache_pre_hash: str
    cache_post_hash: str
    output_hash: str
    usage: UsageVector
    price_version: str
    charge_microunits: int

    def __post_init__(self):
        if self.charge_microunits < 0:
            raise ValueError("charge must be non-negative")


@dataclass(frozen=True)
class ProviderReceipt:
    request_id: str
    call_id: str
    authorization_digest: str
    deployment_id: str
    service_config_hash: str
    hb_handoff_hash: str
    bp_handoff_hash: str
    transformation_hash: str
    input_hash: str
    cache_pre_hash: str
    cache_post_hash: str
    output_hash: str
    usage: UsageVector
    price_version: str
    charge_microunits: int
    billable: bool
    witness_commitment: str
    assurance_mode: str
    assurance_anchor: str
    call_nullifier: str
    provider_id: str
    signature: str = ""
    def signed_payload(self):
        return {k: v for k, v in self.__dict__.items() if k != "signature"}


@dataclass(frozen=True)
class RequestClosure:
    request_id: str
    authorization_digest: str
    lifecycle_root: str
    response_commitment: str
    terminal_call_ids: tuple[str, ...]
    provider_accepted_call_ids: tuple[str, ...]
    harness_id: str
    signature: str = ""
    def signed_payload(self):
        return {k: v for k, v in self.__dict__.items() if k != "signature"}


@dataclass(frozen=True)
class Settlement:
    request_id: str
    closure_hash: str
    handoff_hashes: tuple[str, ...]
    receipt_hashes: tuple[str, ...]
    auxiliary_fees: tuple[int, ...]
    evidence_root: str
    bill_microunits: int
    settlement_nullifier: str
    broker_id: str
    broker_signature: str = ""
    harness_signature: str = ""
    def context(self):
        return {k: v for k, v in self.__dict__.items() if k not in {"broker_signature", "harness_signature"}}


@dataclass(frozen=True)
class AuditResult:
    accepted: bool
    failed_relation: str | None
    responsibility_interval: tuple[str, str] | None
    timings: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceObject:
    request_id: str
    evidence_type: str
    sequence: int
    payload: Any
    call_id: str | None = None
    deployment_id: str | None = None
    created_ns: int = 0
    content_hash: str = ""

    def hash_payload(self):
        return {k: v for k, v in self.__dict__.items() if k != "content_hash"}


@dataclass(frozen=True)
class LedgerEntry:
    sequence: int
    record_id: str
    record_type: str
    payload: Any
    member_id: str
    previous_hash: str
    entry_hash: str = ""
    signature: str = ""

    def hash_payload(self):
        return {k: v for k, v in self.__dict__.items() if k not in {"entry_hash", "signature"}}

    def signed_payload(self):
        return {**self.hash_payload(), "entry_hash": self.entry_hash}


@dataclass(frozen=True)
class WitnessCommitment:
    request_id: str
    call_id: str
    deployment_id: str
    commitment: str

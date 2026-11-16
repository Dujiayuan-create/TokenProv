"""Honest protocol fixture for integration tests and protocol-only benchmarks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tokenprov.common.models import (
    Authorization, DeploymentDescriptor, DeploymentRegistration, LifecycleEvent,
    PricePolicy, ProviderCallEvidence, ProviderReceipt, RequestClosure, Settlement,
    UsageVector,
)
from tokenprov.common.serialization import commitment, digest
from tokenprov.crypto import KeyPair, generate_keypair
from tokenprov.handoff import create_handoff, countersign_handoff
from tokenprov.lifecycle import LifecycleDAG
from tokenprov.provider import issue_receipt, register_deployment
from tokenprov.settlement import create_closure, create_settlement, evidence_root
from tokenprov.user import create_authorization


@dataclass
class ProtocolFixture:
    keys: dict[str, KeyPair]
    authorization: Authorization
    public_transcript: dict[str, Any]
    private_witness: dict[str, Any]


def _handoff(keys, request_id, call_id, direction, boundary, opening, causal=()):
    sender, receiver_label = direction.split("->")
    receiver = "P1" if receiver_label == "P" else receiver_label
    record = create_handoff(request_id, call_id, direction, boundary, opening, causal,
                            sender, receiver, keys[sender].private_key)
    return countersign_handoff(record, boundary, opening, request_id, call_id, direction,
                               keys[sender].public_key, keys[receiver].private_key)


def build_honest_protocol(calls: int = 1, *, request_id: str = "r-fixture",
                          deployment_id: str = "dep-fixture",
                          model_id: str = "deterministic/tokenprov",
                          lifecycle_events: int | None = None) -> ProtocolFixture:
    if calls < 1:
        raise ValueError("fixture requires at least one provider-accepted call")
    minimum_events = 1 + 2 * calls
    lifecycle_events = minimum_events if lifecycle_events is None else lifecycle_events
    if lifecycle_events < minimum_events:
        raise ValueError(f"lifecycle_events must be at least {minimum_events}")
    keys = {name: generate_keypair() for name in ("U", "H", "B", "P1")}
    auth = Authorization(request_id, {"purpose": "protocol-fixture"},
                         commitment("input/v1", "fixture-input", "fixture-open"),
                         {"max_bill_microunits": 1_000_000}, "fixture-nonce", "U")
    auth = create_authorization(auth, keys["U"].private_key)
    auth_digest = digest("authorization/v1", auth)
    dag = LifecycleDAG(request_id, auth_digest)
    root = dag.add(LifecycleEvent(request_id, auth_digest, "e-root", None,
                   "authorization_root", (), digest("payload/v1", {}), "active", "H"),
                   keys["H"].private_key)
    predecessor = digest("lifecycle-event/v1", root)
    for index in range(lifecycle_events - minimum_events):
        workflow = dag.add(LifecycleEvent(request_id, auth_digest, f"e-work-{index}", None,
            "workflow_step", (predecessor,), digest("payload/v1", {"workflow": index}),
            "active", "H"), keys["H"].private_key)
        predecessor = digest("lifecycle-event/v1", workflow)
    call_ids = []
    for index in range(calls):
        call_id = f"c-{index}"
        call_ids.append(call_id)
        created = dag.add(LifecycleEvent(request_id, auth_digest, f"e-{index}-create", call_id,
                          "call_created", (predecessor,), digest("payload/v1", {"index": index}),
                          "active", "H"), keys["H"].private_key)
        predecessor = digest("lifecycle-event/v1", created)
        terminal = dag.add(LifecycleEvent(request_id, auth_digest, f"e-{index}-terminal", call_id,
                           "call_terminal", (predecessor,), digest("payload/v1", {}),
                           "succeeded", "H"), keys["H"].private_key)
        predecessor = digest("lifecycle-event/v1", terminal)

    descriptor = DeploymentDescriptor(model_id, "v1", "model-hash",
                                      "tokenizer-hash", "canonicalization-hash",
                                      "serving-semantics-hash")
    registration = register_deployment(DeploymentRegistration(
        deployment_id, "P1", descriptor,
        commitment("deployment/v1", descriptor, "deployment-open")),
        keys["P1"].private_key)
    price = PricePolicy("fixture-price-v1", n_pf_rate=1, n_out_rate=2)
    handoffs, calls_and_receipts = [], []
    for index, call_id in enumerate(call_ids):
        hb_boundary = {"request_id": request_id, "call_id": call_id,
                       "messages": [{"role": "user", "content": "fixture"}]}
        hb = _handoff(keys, request_id, call_id, "H->B", hb_boundary, f"hb-{index}")
        hb_hash = digest("handoff/v1", hb)
        bp_boundary = {"request_id": request_id, "call_id": call_id,
                       "deployment_id": registration.deployment_id,
                       "preceding_handoff": hb_hash}
        bp = _handoff(keys, request_id, call_id, "B->P", bp_boundary, f"bp-{index}",
                      (hb_hash,))
        bp_hash = digest("handoff/v1", bp)
        handoffs.extend(((hb, hb_boundary, f"hb-{index}"),
                         (bp, bp_boundary, f"bp-{index}")))
        usage = UsageVector(n_pf=16 + index, n_out=4)
        shared = dict(request_id=request_id, call_id=call_id,
                      authorization_digest=auth_digest,
                      deployment_id=registration.deployment_id,
                      service_config_hash="service-config-hash",
                      hb_handoff_hash=hb_hash, bp_handoff_hash=bp_hash,
                      transformation_hash="identity-v1", input_hash=f"input-{index}",
                      cache_pre_hash=f"cache-pre-{index}", cache_post_hash=f"cache-post-{index}",
                      output_hash=f"output-{index}", usage=usage,
                      price_version=price.version, charge_microunits=price.charge(usage))
        call = ProviderCallEvidence(**shared)
        receipt = issue_receipt(ProviderReceipt(
            **shared, billable=True, witness_commitment=f"witness-{index}",
            assurance_mode="signature", assurance_anchor=f"witness-{index}",
            call_nullifier=f"call-nullifier-{index}", provider_id="P1"),
            keys["P1"].private_key)
        calls_and_receipts.append((call, receipt, {"mode": "signature"}))

    closure = create_closure(RequestClosure(
        request_id, auth_digest, dag.root(),
        commitment("response/v1", "fixture-answer", "answer-open"),
        tuple(call_ids), tuple(call_ids), "H"), keys["H"].private_key)
    handoff_records = [item[0] for item in handoffs]
    receipts = [item[1] for item in calls_and_receipts]
    fees = (2,)
    settlement = create_settlement(Settlement(
        request_id, digest("request-closure/v1", closure),
        tuple(digest("handoff/v1", item) for item in handoff_records),
        tuple(digest("provider-receipt/v1", item) for item in receipts), fees,
        evidence_root(handoff_records, receipts, fees),
        sum(item.charge_microunits for item in receipts) + sum(fees),
        "settlement-nullifier", "B"), keys["B"].private_key, keys["H"].private_key)
    public = {
        "keys": {name: pair.public_key for name, pair in keys.items()},
        "registrations": {registration.deployment_id: registration},
        "price_policies": {price.version: price}, "closure": closure,
        "settlement": settlement,
    }
    private = {"lifecycle": dag, "handoffs": handoffs,
               "provider_calls": calls_and_receipts}
    return ProtocolFixture(keys, auth, public, private)

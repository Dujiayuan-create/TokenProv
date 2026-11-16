"""Strong signed-log baseline: disclosed records, independent plaintext checks.

The baseline deliberately supports bilateral receipts, call linkage and exact
coverage. It does not invoke TokenProv's audit() or verify a TEE/ZK artifact.
Signature-mode TokenProv and this baseline can have identical coverage.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from tokenprov.common.models import AuditResult
from tokenprov.common.serialization import digest, verify_opening
from tokenprov.crypto.signing import verify_object
from tokenprov.settlement.core import evidence_root


@dataclass(frozen=True)
class DisclosedLog:
    authorization: object
    keys: dict
    events: tuple
    handoffs: tuple
    calls: tuple
    registrations: dict
    prices: dict
    closure: object
    settlement: object

    @classmethod
    def from_protocol(cls, auth, public, private):
        return cls(auth, public["keys"], tuple(private["lifecycle"].events.values()),
            tuple(private["handoffs"]), tuple((call, receipt) for call, receipt, _ in private["provider_calls"]),
            public["registrations"], public["price_policies"], public["closure"], public["settlement"])


def verify_disclosed_log(log: DisclosedLog) -> bool:
    auth, closure, settlement, keys = log.authorization, log.closure, log.settlement, log.keys
    auth_hash = digest("authorization/v1", auth)
    if not verify_object(keys[auth.user_id], "authorization/v1", auth.signed_payload(), auth.user_signature): return False
    events = {digest("lifecycle-event/v1", event): event for event in log.events}
    if len(events) != len(log.events) or len({x.event_id for x in log.events}) != len(log.events): return False
    roots = [h for h, e in events.items() if e.event_type == "authorization_root"]
    if len(roots) != 1 or events[roots[0]].predecessor_hashes: return False
    children = {h: [] for h in events}
    pending = {}
    created, terminal = set(), set()
    for h, event in events.items():
        if event.request_id != auth.request_id or event.authorization_digest != auth_hash: return False
        if not verify_object(keys[event.issuer], "lifecycle-event/v1", event.signed_payload(), event.signature): return False
        parents = event.predecessor_hashes
        if len(set(parents)) != len(parents) or (h != roots[0] and not parents): return False
        pending[h] = len(parents)
        for parent in parents:
            if parent not in children: return False
            children[parent].append(h)
        if event.event_type == "call_created":
            if not event.call_id or event.call_id in created: return False
            created.add(event.call_id)
        if event.state in {"succeeded", "failed", "aborted"}:
            if not event.call_id or event.call_id in terminal: return False
            terminal.add(event.call_id)
    ready, visited = deque(roots), 0
    while ready:
        current = ready.popleft()
        visited += 1
        for child in children[current]:
            pending[child] -= 1
            if pending[child] == 0: ready.append(child)
    if visited != len(events) or terminal != created: return False
    if closure.request_id != auth.request_id or closure.authorization_digest != auth_hash: return False
    if closure.lifecycle_root != digest("lifecycle-dag-root/v1", sorted(events)): return False
    if len(closure.terminal_call_ids) != len(set(closure.terminal_call_ids)) or set(closure.terminal_call_ids) != created: return False
    accepted = set(closure.provider_accepted_call_ids)
    if len(accepted) != len(closure.provider_accepted_call_ids) or not accepted <= created: return False
    if not verify_object(keys[closure.harness_id], "request-closure/v1", closure.signed_payload(), closure.signature): return False

    boundaries = {}
    for handoff, payload, opening in log.handoffs:
        if handoff.request_id != auth.request_id or payload.get("request_id") != auth.request_id: return False
        if payload.get("call_id") != handoff.call_id or handoff.call_id not in created: return False
        if handoff.direction not in {"H->B", "B->P"}: return False
        if handoff.sender != ("H" if handoff.direction == "H->B" else "B"): return False
        if handoff.direction == "H->B" and handoff.receiver != "B": return False
        if not verify_opening("handoff-boundary/v1", payload, opening, handoff.boundary_commitment): return False
        if not all(verify_object(keys[actor], "handoff/v1", handoff.context(), signature)
            for actor, signature in ((handoff.sender, handoff.sender_signature), (handoff.receiver, handoff.receiver_signature))): return False
        key = (handoff.call_id, handoff.direction)
        if key in boundaries: return False
        boundaries[key] = (handoff, payload)
    observed = {cid for cid, direction in boundaries if direction == "B->P"}
    if observed != accepted: return False
    for cid in accepted:
        hb = boundaries[(cid, "H->B")][0]
        bp = boundaries[(cid, "B->P")][0]
        if digest("handoff/v1", hb) not in bp.causal_references: return False

    receipts, seen_calls, seen_nullifiers = [], set(), set()
    shared = ("request_id", "call_id", "authorization_digest", "deployment_id", "service_config_hash",
        "hb_handoff_hash", "bp_handoff_hash", "transformation_hash", "input_hash", "cache_pre_hash",
        "cache_post_hash", "output_hash", "usage", "price_version", "charge_microunits")
    for call, receipt in log.calls:
        if call.call_id in seen_calls or receipt.call_nullifier in seen_nullifiers: return False
        seen_calls.add(call.call_id); seen_nullifiers.add(receipt.call_nullifier)
        if not receipt.call_nullifier or not receipt.witness_commitment: return False
        if call.request_id != auth.request_id or call.authorization_digest != auth_hash: return False
        if call.call_id not in accepted or any(getattr(call, f) != getattr(receipt, f) for f in shared): return False
        reg = log.registrations[receipt.deployment_id]
        if reg.provider_id != receipt.provider_id: return False
        if not verify_object(keys[receipt.provider_id], "deployment-registration/v1", reg.signed_payload(), reg.signature): return False
        if not verify_object(keys[receipt.provider_id], "provider-receipt/v1", receipt.signed_payload(), receipt.signature): return False
        hb, bp = (boundaries[(call.call_id, direction)][0] for direction in ("H->B", "B->P"))
        if bp.receiver != receipt.provider_id: return False
        if (call.hb_handoff_hash, call.bp_handoff_hash) != (digest("handoff/v1", hb), digest("handoff/v1", bp)): return False
        boundary = boundaries[(call.call_id, "B->P")][1]
        if "deployment_id" in boundary and boundary["deployment_id"] != receipt.deployment_id: return False
        if "provider_call" in boundary:
            request = boundary["provider_call"]
            if request.configuration.provider_id != receipt.provider_id or request.configuration.model_id != reg.descriptor.model_id: return False
            if digest("service-configuration/v1", request.configuration) != receipt.service_config_hash: return False
            if digest("provider-input/v1", request.transformed_messages) != receipt.input_hash: return False
            if digest("transformation-rule/v1", request.transformation_rule) != receipt.transformation_hash: return False
            if boundary.get("registration_hash") != digest("deployment-registration/v1", reg): return False
        if "allowed_provider_ids" in auth.policy and receipt.provider_id not in auth.policy["allowed_provider_ids"]: return False
        if "allowed_model_hashes" in auth.policy and reg.descriptor.model_hash not in auth.policy["allowed_model_hashes"]: return False
        price = log.prices[receipt.price_version]
        amount = price.charge(receipt.usage) if receipt.billable else 0
        if type(receipt.charge_microunits) is not int or amount != receipt.charge_microunits: return False
        receipts.append(receipt)
    if seen_calls != accepted: return False
    if settlement.request_id != auth.request_id or settlement.closure_hash != digest("request-closure/v1", closure): return False
    if any(type(x) is not int or x < 0 for x in settlement.auxiliary_fees): return False
    total = sum(x.charge_microunits for x in receipts) + sum(settlement.auxiliary_fees)
    maximum = auth.policy.get("max_bill_microunits")
    if type(maximum) is not int or not 0 <= total <= maximum or settlement.bill_microunits != total: return False
    if "max_calls" in auth.policy and len(created) > auth.policy["max_calls"]: return False
    handoffs = [x[0] for x in log.handoffs]
    if settlement.handoff_hashes != tuple(digest("handoff/v1", h) for h in handoffs): return False
    if settlement.receipt_hashes != tuple(digest("provider-receipt/v1", r) for r in receipts): return False
    if settlement.evidence_root != evidence_root(handoffs, receipts, settlement.auxiliary_fees): return False
    return all(verify_object(keys[actor], "settlement/v1", settlement.context(), signature)
        for actor, signature in ((settlement.broker_id, settlement.broker_signature), (closure.harness_id, settlement.harness_signature)))


def audit_signed_log_plus(auth, public, private) -> AuditResult:
    start = time.perf_counter_ns()
    try:
        accepted = verify_disclosed_log(DisclosedLog.from_protocol(auth, public, private))
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        accepted = False
    return AuditResult(accepted, None if accepted else "SignedLogPlus", None,
                       {"SignedLogPlus": time.perf_counter_ns() - start})

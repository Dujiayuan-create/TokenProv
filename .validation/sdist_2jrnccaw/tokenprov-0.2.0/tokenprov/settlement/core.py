from dataclasses import replace
from tokenprov.common.models import RequestClosure, Settlement
from tokenprov.common.merkle import MerkleLeaf, MerkleTree
from tokenprov.common.serialization import digest
from tokenprov.crypto.signing import sign_object, verify_object


def create_closure(closure: RequestClosure, harness_private_key: str) -> RequestClosure:
    if (len(set(closure.terminal_call_ids)) != len(closure.terminal_call_ids)
            or len(set(closure.provider_accepted_call_ids)) != len(closure.provider_accepted_call_ids)
            or not set(closure.provider_accepted_call_ids).issubset(closure.terminal_call_ids)):
        raise ValueError("closure call coverage is invalid")
    return replace(closure, signature=sign_object(harness_private_key, "request-closure/v1", closure.signed_payload()))


def evidence_root(handoffs, receipts, fees) -> str:
    values = ([('handoff', h) for h in handoffs]
              + [('receipt', r) for r in receipts]
              + [('fee', {"amount_microunits": f}) for f in fees])
    return MerkleTree(MerkleLeaf(kind, i, value) for i, (kind, value) in enumerate(values)).root


def evidence_tree(handoffs, receipts, fees) -> MerkleTree:
    values = ([('handoff', h) for h in handoffs]
              + [('receipt', r) for r in receipts]
              + [('fee', {"amount_microunits": f}) for f in fees])
    return MerkleTree(MerkleLeaf(kind, i, value) for i, (kind, value) in enumerate(values))


def create_settlement(base: Settlement, broker_private_key: str, harness_private_key: str) -> Settlement:
    if base.bill_microunits < 0 or any(fee < 0 for fee in base.auxiliary_fees):
        raise ValueError("settlement amounts must be non-negative")
    first = replace(base, broker_signature=sign_object(broker_private_key, "settlement/v1", base.context()))
    return replace(first, harness_signature=sign_object(harness_private_key, "settlement/v1", first.context()))


def verify_settlement(settlement, closure, handoffs, receipts, broker_public_key, harness_public_key,
                      seen_settlement_nullifiers: set[str] | None = None) -> bool:
    seen = seen_settlement_nullifiers if seen_settlement_nullifiers is not None else set()
    accepted = [r for r in receipts if r.request_id == settlement.request_id]
    call_ids = [r.call_id for r in accepted]
    expected_calls = list(closure.provider_accepted_call_ids)
    charge = sum(r.charge_microunits for r in accepted) + sum(settlement.auxiliary_fees)
    ok = (settlement.request_id == closure.request_id
          and settlement.bill_microunits >= 0
          and all(fee >= 0 for fee in settlement.auxiliary_fees)
          and verify_object(harness_public_key, "request-closure/v1", closure.signed_payload(), closure.signature)
          and settlement.closure_hash == digest("request-closure/v1", closure)
          and len(closure.terminal_call_ids) == len(set(closure.terminal_call_ids))
          and set(closure.provider_accepted_call_ids).issubset(closure.terminal_call_ids)
          and len(call_ids) == len(set(call_ids)) and sorted(call_ids) == sorted(expected_calls)
          and len({r.call_nullifier for r in accepted}) == len(accepted)
          and tuple(digest("handoff/v1", h) for h in handoffs) == settlement.handoff_hashes
          and tuple(digest("provider-receipt/v1", r) for r in receipts) == settlement.receipt_hashes
          and evidence_root(handoffs, receipts, settlement.auxiliary_fees) == settlement.evidence_root
          and charge == settlement.bill_microunits
          and settlement.settlement_nullifier not in seen
          and verify_object(broker_public_key, "settlement/v1", settlement.context(), settlement.broker_signature)
          and verify_object(harness_public_key, "settlement/v1", settlement.context(), settlement.harness_signature))
    if ok: seen.add(settlement.settlement_nullifier)
    return ok

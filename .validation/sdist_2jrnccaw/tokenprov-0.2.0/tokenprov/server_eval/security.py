"""Adaptive, valid-signature protocol attacks against common evidence.

Ground truth is the original independent acceptances and integer invoice, not a
validator's failed-relation label. This suite makes no full-inference claim.
"""
from __future__ import annotations

import random
import time
from dataclasses import replace

from tokenprov.audit import audit
from tokenprov.audit.signedlog import audit_signed_log_plus
from tokenprov.common.serialization import digest
from tokenprov.crypto import sign_object, verify_object
from tokenprov.provider.receipts import verify_receipt
from tokenprov.server_eval.artifacts import RunArtifacts
from tokenprov.settlement import create_closure, create_settlement, evidence_root
from tokenprov.workloads import build_honest_protocol

ATTACKS = ("bill_delta", "duplicate_receipt", "omit_receipt", "omit_acceptance", "cross_call_splice", "wrong_deployment")


def mutate(fixture, attack, index=0, delta=1):
    """H+B are corrupt; P1 is additionally corrupt only for provider attacks.

    Honest U authorization and original H->B boundaries remain unchanged. The
    generator repairs outer settlement hashes/signatures rather than leaving
    trivially stale signatures. No honest party's signing key is used.
    """
    public = dict(fixture.public_transcript)
    private = {**fixture.private_witness, "provider_calls": list(fixture.private_witness["provider_calls"])}
    pairs = private["provider_calls"]
    index %= len(pairs)
    corrupt = {"H", "B"}
    if attack == "duplicate_receipt": pairs.append(pairs[index])
    elif attack == "omit_receipt": pairs.pop(index)
    elif attack == "omit_acceptance":
        removed = pairs.pop(index)[0].call_id
        # Independent signed provider acceptance remains in the evidence.
        public["closure"] = create_closure(replace(public["closure"],
            provider_accepted_call_ids=tuple(cid for cid in public["closure"].provider_accepted_call_ids if cid != removed)),
            fixture.keys["H"].private_key)
    elif attack == "cross_call_splice":
        if len(pairs) < 2: raise ValueError("splicing requires two calls")
        call, receipt, artifact = pairs[index]
        other = pairs[(index + 1) % len(pairs)][0]
        corrupt.add("P1")
        changed = {"input_hash": other.input_hash, "output_hash": other.output_hash,
                   "hb_handoff_hash": other.hb_handoff_hash, "bp_handoff_hash": other.bp_handoff_hash}
        receipt = replace(receipt, **changed, signature="")
        receipt = replace(receipt, signature=sign_object(fixture.keys["P1"].private_key,
            "provider-receipt/v1", receipt.signed_payload()))
        pairs[index] = (replace(call, **changed), receipt, artifact)
    elif attack == "wrong_deployment":
        call, receipt, artifact = pairs[index]
        corrupt.add("P1")
        receipt = replace(receipt, deployment_id="unauthorized-deployment", signature="")
        receipt = replace(receipt, signature=sign_object(fixture.keys["P1"].private_key,
            "provider-receipt/v1", receipt.signed_payload()))
        pairs[index] = (replace(call, deployment_id=receipt.deployment_id), receipt, artifact)
    elif attack != "bill_delta": raise ValueError("unknown attack")
    handoffs = [h for h, _, _ in private["handoffs"]]
    receipts = [r for _, r, _ in pairs]
    settlement = public["settlement"]
    amount = sum(r.charge_microunits for r in receipts) + sum(settlement.auxiliary_fees)
    if attack == "bill_delta": amount += delta
    public["settlement"] = create_settlement(replace(settlement,
        closure_hash=digest("request-closure/v1", public["closure"]),
        handoff_hashes=tuple(digest("handoff/v1", h) for h in handoffs),
        receipt_hashes=tuple(digest("provider-receipt/v1", r) for r in receipts),
        evidence_root=evidence_root(handoffs, receipts, settlement.auxiliary_fees), bill_microunits=amount),
        fixture.keys["B"].private_key, fixture.keys["H"].private_key)
    return public, private, corrupt


def signatures_valid(fixture, public, private):
    keys = fixture.public_transcript["keys"]
    closure, settlement = public["closure"], public["settlement"]
    return (verify_object(keys["H"], "request-closure/v1", closure.signed_payload(), closure.signature)
        and verify_object(keys["B"], "settlement/v1", settlement.context(), settlement.broker_signature)
        and verify_object(keys["H"], "settlement/v1", settlement.context(), settlement.harness_signature)
        and all(verify_object(keys[r.provider_id], "provider-receipt/v1", r.signed_payload(), r.signature)
                for _, r, _ in private["provider_calls"]))


def oracle_violation(fixture, public, private):
    expected = {r.call_id: r for _, r, _ in fixture.private_witness["provider_calls"]}
    actual = [r for _, r, _ in private["provider_calls"]]
    ids = [r.call_id for r in actual]
    if len(ids) != len(set(ids)) or set(ids) != set(expected): return True
    if set(public["closure"].provider_accepted_call_ids) != set(expected): return True
    fields = ("input_hash", "output_hash", "hb_handoff_hash", "bp_handoff_hash", "deployment_id", "usage", "charge_microunits")
    if any(any(getattr(r, f) != getattr(expected[r.call_id], f) for f in fields) for r in actual): return True
    return public["settlement"].bill_microunits != fixture.public_transcript["settlement"].bill_microunits


def weak_signed_log(fixture, public, private):
    # The same signed evidence is checked, never a separate toy mutation.
    keys = public["keys"]
    return all(verify_object(keys[r.provider_id], "provider-receipt/v1", r.signed_payload(), r.signature)
               for _, r, _ in private["provider_calls"])


def run_security(config, *, trials=100, seeds=(42, 43, 44, 45, 46), queries=16, calls=8, progress=print):
    if calls < 2 or trials < 1 or queries < 1: raise ValueError("calls>=2, trials>=1 and queries>=1 required")
    artifacts = RunArtifacts(config, "protocol_security_microbenchmark")
    totals = {}
    try:
        for seed in seeds:
            progress(f"security seed={seed} trials={trials} budget={queries}")
            for trial in range(trials):
                fixture = build_honest_protocol(calls=calls, request_id=f"security-{seed}-{trial}")
                for system in ("SignedLog", "SignedLogPlus", "TokenProv-Full"):
                    def check(public, private):
                        if system == "SignedLog": return weak_signed_log(fixture, public, private), None
                        decision = (audit_signed_log_plus if system == "SignedLogPlus" else audit)(fixture.authorization, public, private)
                        return decision.accepted, decision.failed_relation
                    benign, _ = check(fixture.public_transcript, fixture.private_witness)
                    artifacts.append("security.jsonl", {"seed": seed, "trial": trial, "system": system,
                        "kind": "benign", "accepted": benign, "measurement_type": "PROTOCOL_MICROBENCHMARK"})
                    rng = random.Random(seed * 100000 + trial)
                    choices, feedback = list(ATTACKS), None
                    succeeded = False
                    for query in range(queries):
                        # Diagnostic feedback changes the next attack family: this
                        # is an adaptive white-box-feedback search, not random repeats.
                        if feedback == "R_hand": choices = ["duplicate_receipt", "bill_delta", "omit_receipt"]
                        elif feedback == "R_set": choices = ["cross_call_splice", "wrong_deployment", "omit_acceptance"]
                        else: choices = list(ATTACKS)
                        attack = choices[rng.randrange(len(choices))]
                        public, private, corrupt = mutate(fixture, attack, rng.randrange(calls), rng.randint(1, 100))
                        valid, violation = signatures_valid(fixture, public, private), oracle_violation(fixture, public, private)
                        if not valid or not violation: raise RuntimeError("attack construction/oracle failure")
                        start = time.perf_counter_ns(); accepted, feedback = check(public, private)
                        artifacts.append("security.jsonl", {"seed": seed, "trial": trial, "system": system,
                            "kind": "adaptive_query", "query": query + 1, "attack": attack,
                            "corrupt_roles": sorted(corrupt), "valid_signatures": valid,
                            "oracle_violation": violation, "accepted": accepted, "first_failed_relation": feedback,
                            "audit_ns": time.perf_counter_ns() - start, "measurement_type": "PROTOCOL_MICROBENCHMARK"})
                        if accepted: succeeded = True; break
                    artifacts.append("security.jsonl", {"seed": seed, "trial": trial, "system": system,
                        "kind": "adaptive_episode", "budget": queries, "queries_used": query + 1,
                        "attack_succeeded": succeeded, "measurement_type": "PROTOCOL_MICROBENCHMARK"})
                    total = totals.setdefault(system, {"episodes": 0, "successful_attacks": 0, "benign_false_rejections": 0})
                    total["episodes"] += 1; total["successful_attacks"] += succeeded
                    total["benign_false_rejections"] += not benign
        artifacts.write_json("summary.json", {"systems": totals,
            "scope": "observable authenticated protocol violations; no inference or semantic-necessity guarantee",
            "feedback": "first-failed-relation where exposed", "queries_per_episode": queries})
        artifacts.status("completed")
        return artifacts.root, totals
    except BaseException as exc:
        artifacts.status("failed", error_type=type(exc).__name__); raise

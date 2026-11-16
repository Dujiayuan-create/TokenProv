from dataclasses import replace

import pytest

from tokenprov.assurance import DeferredWitnessRegistry
from tokenprov.audit import audit
from tokenprov.common.merkle import MerkleLeaf, MerkleTree, verify_inclusion
from tokenprov.common.models import (
    Authorization, DeploymentDescriptor, DeploymentRegistration, LifecycleEvent,
    PricePolicy, ProviderCallEvidence, ProviderReceipt, RequestClosure, Settlement,
    UsageVector,
)
from tokenprov.common.serialization import commitment, digest, verify_opening
from tokenprov.crypto import generate_keypair
from tokenprov.evidence import LocalEvidenceRepository
from tokenprov.handoff import create_handoff, countersign_handoff, verify_handoff
from tokenprov.ledger import LocalAuthenticatedLedger
from tokenprov.lifecycle import LifecycleDAG, LifecycleError
from tokenprov.provider import issue_receipt, register_deployment, verify_provider, verify_receipt
from tokenprov.settlement import create_closure, create_settlement, evidence_root, evidence_tree, verify_settlement
from tokenprov.user import create_authorization, verify_authorization


def keys():
    return {name: generate_keypair() for name in ("U", "H", "B", "P1")}


def bilateral(k, rid, cid, direction, boundary, opening, causal=()):
    sender, receiver = direction.split("->")
    handoff = create_handoff(rid, cid, direction, boundary, opening, causal, sender,
                             "P1" if receiver == "P" else receiver,
                             k[sender].private_key)
    return countersign_handoff(handoff, boundary, opening, rid, cid, direction,
                               k[sender].public_key,
                               k["P1" if receiver == "P" else receiver].private_key)


def build(calls=1, billable=True, max_bill=10_000):
    k = keys()
    rid = "r-1"
    auth = Authorization(rid, {"purpose": "test"},
                         commitment("input/v1", "secret", "open"),
                         {"max_bill_microunits": max_bill}, "nonce-r1", "U")
    auth = create_authorization(auth, k["U"].private_key)
    auth_digest = digest("authorization/v1", auth)
    dag = LifecycleDAG(rid, auth_digest)
    root = LifecycleEvent(rid, auth_digest, "e0", None, "authorization_root", (),
                          digest("payload/v1", {}), "active", "H")
    root = dag.add(root, k["H"].private_key)
    predecessor = digest("lifecycle-event/v1", root)
    call_ids = []
    for i in range(calls):
        cid = f"c-{i}"
        call_ids.append(cid)
        created = LifecycleEvent(rid, auth_digest, f"e{i}c", cid, "call_created",
                                 (predecessor,), digest("payload/v1", {"i": i}),
                                 "active", "H")
        created = dag.add(created, k["H"].private_key)
        predecessor = digest("lifecycle-event/v1", created)
        terminal = LifecycleEvent(rid, auth_digest, f"e{i}t", cid, "call_terminal",
                                  (predecessor,), digest("payload/v1", {}),
                                  "succeeded", "H")
        terminal = dag.add(terminal, k["H"].private_key)
        predecessor = digest("lifecycle-event/v1", terminal)

    descriptor = DeploymentDescriptor("Qwen/test", "local", "mh", "th", "ch", "sh")
    registration = DeploymentRegistration(
        "dep-1", "P1", descriptor,
        commitment("deployment/v1", descriptor, "dep-open"),
    )
    registration = register_deployment(registration, k["P1"].private_key)
    policy = PricePolicy("price-v1", n_pf_rate=1, n_out_rate=1)
    handoffs = []
    receipts = []
    provider_calls = []
    for i, cid in enumerate(call_ids):
        hb_boundary = {"request_id": rid, "call_id": cid,
                       "messages": [{"role": "user", "content": "x"}]}
        hb = bilateral(k, rid, cid, "H->B", hb_boundary, f"hb-open-{i}")
        hb_hash = digest("handoff/v1", hb)
        bp_boundary = {"request_id": rid, "call_id": cid, "deployment_id": "dep-1",
                       "preceding_handoff": hb_hash}
        bp = bilateral(k, rid, cid, "B->P", bp_boundary, f"bp-open-{i}", (hb_hash,))
        bp_hash = digest("handoff/v1", bp)
        handoffs.extend(((hb, hb_boundary, f"hb-open-{i}"),
                         (bp, bp_boundary, f"bp-open-{i}")))
        usage = UsageVector(n_pf=3, n_out=2)
        charge = policy.charge(usage) if billable else 0
        shared = dict(
            request_id=rid, call_id=cid, authorization_digest=auth_digest,
            deployment_id="dep-1", service_config_hash="svc", hb_handoff_hash=hb_hash,
            bp_handoff_hash=bp_hash, transformation_hash="transform-v1",
            input_hash=f"in-{i}", cache_pre_hash="pre", cache_post_hash="post",
            output_hash=f"out-{i}", usage=usage, price_version="price-v1",
            charge_microunits=charge,
        )
        call = ProviderCallEvidence(**shared)
        receipt = ProviderReceipt(
            **shared, billable=billable, witness_commitment=f"wit-{i}",
            assurance_mode="signature", assurance_anchor=f"wit-{i}",
            call_nullifier=f"null-{i}", provider_id="P1",
        )
        receipt = issue_receipt(receipt, k["P1"].private_key)
        receipts.append(receipt)
        provider_calls.append((call, receipt, {"mode": "signature"}))
    closure = RequestClosure(
        rid, auth_digest, dag.root(), commitment("response/v1", "response", "ans-open"),
        tuple(call_ids), tuple(call_ids), "H",
    )
    closure = create_closure(closure, k["H"].private_key)
    handoff_records = [item[0] for item in handoffs]
    base = Settlement(
        rid, digest("request-closure/v1", closure),
        tuple(digest("handoff/v1", h) for h in handoff_records),
        tuple(digest("provider-receipt/v1", receipt) for receipt in receipts),
        (2,), evidence_root(handoff_records, receipts, (2,)),
        sum(receipt.charge_microunits for receipt in receipts) + 2,
        "settle-null", "B",
    )
    settlement = create_settlement(base, k["B"].private_key, k["H"].private_key)
    public_keys = {name: pair.public_key for name, pair in k.items()}
    public = {
        "keys": public_keys, "registrations": {"dep-1": registration},
        "price_policies": {policy.version: policy}, "closure": closure,
        "settlement": settlement,
    }
    private = {"lifecycle": dag, "handoffs": handoffs, "provider_calls": provider_calls}
    return k, auth, public, private


def test_authorization_signature_and_commitment():
    k, auth, _, _ = build()
    assert verify_authorization(auth, k["U"].public_key)
    assert not verify_authorization(replace(auth, nonce="changed"), k["U"].public_key)
    assert verify_opening("input/v1", "secret", "open", auth.input_commitment)
    assert not verify_opening("input/v1", "wrong", "open", auth.input_commitment)


def test_lifecycle_unique_eid_cid_terminal_and_closed():
    k, _, public, private = build(2)
    dag = private["lifecycle"]
    assert dag.validate(public["keys"])
    existing = next(iter(dag.events.values()))
    with pytest.raises(LifecycleError):
        dag.add(existing, k["H"].private_key)


def test_lifecycle_rejects_unknown_predecessor_duplicate_cid_and_terminal():
    k, auth, _, private = build()
    dag = private["lifecycle"]
    bad = LifecycleEvent(auth.request_id, digest("authorization/v1", auth), "new", "c-new",
                         "call_created", ("unknown",), "p", "active", "H")
    with pytest.raises(LifecycleError):
        dag.add(bad, k["H"].private_key)
    predecessor = list(dag.hashes)[-1]
    duplicate_cid = replace(bad, event_id="new2", call_id="c-0",
                            predecessor_hashes=(predecessor,))
    with pytest.raises(LifecycleError):
        dag.add(duplicate_cid, k["H"].private_key)
    duplicate_terminal = replace(bad, event_id="new3", call_id="c-0",
                                 event_type="call_terminal", predecessor_hashes=(predecessor,),
                                 state="failed")
    with pytest.raises(LifecycleError):
        dag.add(duplicate_terminal, k["H"].private_key)


def test_handoff_bilateral_causality_and_tamper():
    k, _, _, private = build()
    hb, boundary, opening = private["handoffs"][0]
    bp, bp_boundary, bp_opening = private["handoffs"][1]
    assert verify_handoff(hb, boundary, opening, k["H"].public_key, k["B"].public_key)
    assert verify_handoff(bp, bp_boundary, bp_opening, k["B"].public_key,
                          k["P1"].public_key)
    assert digest("handoff/v1", hb) in bp.causal_references
    assert not verify_handoff(hb, {**boundary, "call_id": "evil"}, opening,
                              k["H"].public_key, k["B"].public_key)


def test_deployment_receipt_nonbillable_and_complete_r_link():
    k, _, public, private = build(billable=False)
    call, receipt, artifact = private["provider_calls"][0]
    assert verify_receipt(receipt, k["P1"].public_key, public["registrations"])
    assert not verify_receipt(replace(receipt, deployment_id="other"),
                              k["P1"].public_key, public["registrations"])
    assert not verify_receipt(replace(receipt, output_hash="tampered"),
                              k["P1"].public_key, public["registrations"])
    assert verify_provider(call, receipt, artifact, k["P1"].public_key,
                           public["registrations"], public["price_policies"])
    for field in ("request_id", "call_id", "authorization_digest", "deployment_id",
                  "service_config_hash", "hb_handoff_hash", "bp_handoff_hash",
                  "transformation_hash", "input_hash", "cache_pre_hash", "cache_post_hash",
                  "output_hash", "usage", "price_version", "charge_microunits"):
        value = "splice" if field != "usage" else UsageVector(n_out=99)
        if field == "charge_microunits": value = 1
        assert not verify_provider(replace(call, **{field: value}), receipt, artifact,
                                   k["P1"].public_key, public["registrations"],
                                   public["price_policies"])
    with pytest.raises(ValueError):
        issue_receipt(replace(receipt, charge_microunits=1, signature=""), k["P1"].private_key)


def test_settlement_coverage_bill_nullifier_and_response_commitment():
    k, _, public, private = build(2)
    settlement, closure = public["settlement"], public["closure"]
    handoffs = [item[0] for item in private["handoffs"]]
    receipts = [item[1] for item in private["provider_calls"]]
    seen = set()
    assert verify_opening("response/v1", "response", "ans-open", closure.response_commitment)
    assert verify_settlement(settlement, closure, handoffs, receipts,
                             k["B"].public_key, k["H"].public_key, seen)
    assert not verify_settlement(settlement, closure, handoffs, receipts,
                                 k["B"].public_key, k["H"].public_key, seen)
    assert not verify_settlement(settlement, closure, handoffs, receipts[:-1],
                                 k["B"].public_key, k["H"].public_key)
    assert not verify_settlement(replace(settlement, bill_microunits=999), closure,
                                 handoffs, receipts, k["B"].public_key, k["H"].public_key)


def test_merkle_membership_and_typed_leaf_rejection():
    _, _, public, private = build(2)
    handoffs = [item[0] for item in private["handoffs"]]
    receipts = [item[1] for item in private["provider_calls"]]
    tree = evidence_tree(handoffs, receipts, (2,))
    index = len(handoffs)
    leaf = MerkleLeaf("receipt", index, receipts[0])
    proof = tree.prove(index)
    assert tree.root == public["settlement"].evidence_root
    assert verify_inclusion(leaf, proof, tree.root)
    assert not verify_inclusion(MerkleLeaf("handoff", index, receipts[0]), proof, tree.root)


def test_deferred_witness_commitment_is_fixed_and_not_a_zk_claim():
    registry = DeferredWitnessRegistry()
    fixed = registry.fix("r-1", "c-0", "dep-1", {"tokens": [1, 2]}, "opening")
    assert registry.verify_opening(fixed, {"tokens": [1, 2]}, "opening")
    assert not registry.verify_opening(fixed, {"tokens": [9]}, "opening")
    with pytest.raises(ValueError):
        registry.fix("r-1", "c-0", "dep-1", {"tokens": [9]}, "other")


def test_local_evidence_repository_roundtrip_and_metadata(tmp_path):
    repository = LocalEvidenceRepository(tmp_path / "evidence")
    stored = repository.put("r-1", "receipts", {"charge": 5}, call_id="c-0",
                            deployment_id="dep-1")
    loaded = repository.get("r-1", "receipts", stored.content_hash)
    assert loaded["request_id"] == "r-1" and loaded["call_id"] == "c-0"
    assert loaded["deployment_id"] == "dep-1" and loaded["payload"] == {"charge": 5}
    assert repository.request_bytes("r-1") > 0
    with pytest.raises(ValueError):
        repository.put("../escape", "receipts", {})


def test_local_authenticated_ledger_order_membership_finality_and_reload(tmp_path):
    pair = generate_keypair()
    path = tmp_path / "ledger.jsonl"
    ledger = LocalAuthenticatedLedger(path, {"H": pair.public_key})
    first = ledger.append("auth-r1", "authorization", {"root": "a"}, "H", pair.private_key)
    second = ledger.append("close-r1", "closure", {"root": "b"}, "H", pair.private_key)
    assert second.previous_hash == first.entry_hash
    assert ledger.order() == ("auth-r1", "close-r1")
    assert ledger.verify_membership("auth-r1") and ledger.wait_finality("close-r1")
    assert LocalAuthenticatedLedger(path, {"H": pair.public_key}).order() == ledger.order()
    with pytest.raises(ValueError):
        ledger.append("auth-r1", "authorization", {}, "H", pair.private_key)
    with pytest.raises(ValueError):
        ledger.append("bad-key", "authorization", {}, "H", generate_keypair().private_key)


def test_audit_success_and_failure_localization():
    _, auth, public, private = build(2)
    good = audit(auth, public, private)
    assert good.accepted and good.failed_relation is None
    bad_public = {**public, "settlement": replace(public["settlement"], bill_microunits=999)}
    bad = audit(auth, bad_public, private)
    assert not bad.accepted and bad.failed_relation == "R_set"
    call, receipt, artifact = private["provider_calls"][0]
    bad_private = {**private, "provider_calls": [
        (replace(call, input_hash="splice"), receipt, artifact),
        *private["provider_calls"][1:],
    ]}
    bad = audit(auth, public, bad_private)
    assert not bad.accepted and bad.failed_relation == "R_prov"


def test_audit_rejects_bill_above_authorization_policy():
    _, auth, public, private = build(max_bill=1)
    result = audit(auth, public, private)
    assert not result.accepted and result.failed_relation == "R_set"

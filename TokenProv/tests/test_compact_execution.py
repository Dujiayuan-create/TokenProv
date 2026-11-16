import concurrent.futures
import json
import os
import stat

import pytest

from tokenprov.broker import NativeTokenProvBroker
from tokenprov.common.models import DeploymentDescriptor, ServiceConfiguration
from tokenprov.common.models import (Authorization, Handoff, LifecycleEvent, ProviderCallEvidence,
    ProviderCallRequest, ProviderReceipt, RequestClosure, Settlement, UsageVector)
from tokenprov.common.serialization import canonical_bytes, digest
from tokenprov.crypto import generate_keypair
from tokenprov.harness import EvaluationHarness
from tokenprov.ledger import LocalAuthenticatedLedger
from tokenprov.server_eval.artifacts import RunArtifacts
from tokenprov.server_eval.audit_jobs import AuditQueue
from tokenprov.server_eval.config import AuditSettings, Config, Endpoint
from tokenprov.settlement import evidence_root
from tokenprov.audit import audit
from tokenprov.lifecycle import LifecycleDAG


def broker():
    configs = [ServiceConfiguration(p, m, None, {}, {}, {}, "local-research-v1")
               for p, m in (("P1", "model-a"), ("P2", "model-b"))]
    def forward(call):
        return {"model": call.configuration.model_id, "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 1}}
    return NativeTokenProvBroker(configs, {c.provider_id: forward for c in configs})


def harness(path):
    descriptors = {c.provider_id: DeploymentDescriptor(c.model_id, "v1", "test-model", "test-tokenizer", "canon", "serving")
                   for c in broker().configurations}
    return EvaluationHarness(broker(), path, model_reference="test", model_revision="v1",
        model_hash="test-model", tokenizer_hash="test-tokenizer", compact=True, provider_descriptors=descriptors)


def audit_saved_bundle(payload, base):
    auth = Authorization(**payload["auth"])
    dag = LifecycleDAG(auth.request_id, digest("authorization/v1", auth))
    events = [LifecycleEvent(**value) for value in payload["lifecycle"]]
    dag.events = {e.event_id: e for e in events}
    dag.hashes = {digest("lifecycle-event/v1", e): e.event_id for e in events}
    dag.call_ids = {e.call_id for e in events if e.event_type == "call_created"}
    handoffs = []
    for h, boundary, opening in payload["handoffs"]:
        boundary = dict(boundary)
        if "provider_call" in boundary:
            call = boundary["provider_call"]
            boundary["provider_call"] = ProviderCallRequest(**{**call,
                "configuration": ServiceConfiguration(**call["configuration"])})
        handoffs.append((Handoff(**h), boundary, opening))
    calls = [(ProviderCallEvidence(**{**c, "usage": UsageVector(**c["usage"])}),
              ProviderReceipt(**{**r, "usage": UsageVector(**r["usage"])}), a)
             for c, r, a in payload["provider_calls"]]
    settlement = {**payload["settlement"]}
    for name in ("handoff_hashes", "receipt_hashes", "auxiliary_fees"):
        settlement[name] = tuple(settlement[name])
    return audit(auth, {"keys": {name: key.public_key for name, key in base.keys.items()},
        "registrations": base.registrations, "price_policies": {base.price.version: base.price},
        "closure": RequestClosure(**payload["closure"]), "settlement": Settlement(**settlement)},
        {"lifecycle": dag, "handoffs": handoffs, "provider_calls": calls})


def test_shared_deployment_keeps_concurrent_request_evidence_isolated(tmp_path):
    base = harness(tmp_path / "service")
    def execute(index):
        return base.for_broker(broker()).execute(f"request-{index}",
            ({"role": "user", "content": f"input-{index}"},), calls_per_request=3)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(execute, range(6)))
    assert all(r.accepted and r.actual_calls == 3 for r in results)
    nonces = set()
    for index, result in enumerate(results):
        assert [r["provider_id"] for r in result.call_records] == ["P1", "P2", "P1"]
        files = list((base.repository.root / f"request-{index}").rglob("*.json"))
        assert len(files) == 1 and files[0].stat().st_size == result.repository_bytes
        stored = base.repository.get(f"request-{index}", "bundle", result.witness_bundle["content_hash"])
        payload = stored["payload"]
        assert audit_saved_bundle(payload, base).accepted  # Verify the durable copy independently.
        nonces.add(payload["auth"]["nonce"])
        assert payload["settlement"]["bill_microunits"] == 18
        assert payload["settlement"]["evidence_root"] == evidence_root(
            [h[0] for h in payload["handoffs"]], [c[1] for c in payload["provider_calls"]], ())
        assert all(c[0]["request_id"] == f"request-{index}" for c in payload["provider_calls"])
        entries = [base.ledger.get(f"request-{index}-{suffix}") for suffix in ("auth", "close", "settle")]
        assert all(entries) and sum(len(canonical_bytes(e)) + 1 for e in entries) == result.ledger_bytes
    assert len(nonces) == 6
    assert len(base.ledger.order()) == 2 + 6 * 3  # Two registrations, once per deployment.
    restored = LocalAuthenticatedLedger(base.ledger.path, base.ledger.member_keys)
    assert restored.order() == base.ledger.order()


def test_corrupt_retained_bundle_is_rejected_before_proving(tmp_path):
    cfg = Config(tmp_path / "config.yaml", (Endpoint("P1", "test", base_url="http://localhost"),),
        tmp_path / "server_runs", audit=AuditSettings(probability=1, workers=1, queue_capacity=3))
    artifacts = RunArtifacts(cfg, "fixture")
    base = harness(artifacts.root / "service")
    result = base.execute("request", ({"role": "user", "content": "test"},), calls_per_request=3)
    from pathlib import Path
    path = Path(result.witness_bundle["path"])
    value = json.loads(path.read_text())
    value["payload"]["provider_witnesses"][0]["witness"]["shared"]["charge_microunits"] = 999
    path.write_text(json.dumps(value))
    queue = AuditQueue(artifacts, cfg.audit, fixture=True)
    assert queue.submit_request({"request_id": "request"}, result.proof_inputs,
        selected=True, settled_ns=0, bundle=result.witness_bundle) == 3
    queue.close()
    assert queue.failures == 3 and queue.completed == 0


def test_ledger_batch_validates_all_records_and_retains_authenticated_chain(tmp_path, monkeypatch):
    keys = generate_keypair()
    ledger = LocalAuthenticatedLedger(tmp_path / "ledger.jsonl", {"H": keys.public_key})
    records = tuple((f"r-{i}", "test", {"i": i}, "H", keys.private_key) for i in range(3))
    with pytest.raises(ValueError):
        ledger.append_batch((*records, ("invalid", "test", {}, "unknown", keys.private_key)))
    assert not ledger.path.exists() and ledger.order() == ()
    sync = os.fsync
    synced_files = []
    def observed_sync(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode): synced_files.append(fd)
        return sync(fd)
    monkeypatch.setattr(os, "fsync", observed_sync)
    assert len(ledger.append_batch(records)) == 3
    assert len(synced_files) == 1
    assert all(ledger.wait_finality(f"r-{i}") for i in range(3))
    assert LocalAuthenticatedLedger(ledger.path, ledger.member_keys).order() == ledger.order()
    with pytest.raises(ValueError): ledger.append_batch(records)


def test_evidence_sync_failure_prevents_settlement_persistence(tmp_path, monkeypatch):
    base = harness(tmp_path / "service")
    before = base.ledger.path.read_bytes()
    def failed_sync(fd): raise OSError("simulated storage failure")
    monkeypatch.setattr(os, "fsync", failed_sync)
    with pytest.raises(OSError, match="storage failure"):
        base.execute("request", ({"role": "user", "content": "test"},), calls_per_request=1)
    assert base.ledger.path.read_bytes() == before


def test_uncertain_ledger_write_cannot_continue_from_stale_head(tmp_path, monkeypatch):
    keys = generate_keypair()
    ledger = LocalAuthenticatedLedger(tmp_path / "ledger.jsonl", {"H": keys.public_key})
    sync = os.fsync
    def failed_sync(fd): raise OSError("uncertain write")
    monkeypatch.setattr(os, "fsync", failed_sync)
    with pytest.raises(OSError): ledger.append("first", "test", {}, "H", keys.private_key)
    monkeypatch.setattr(os, "fsync", sync)
    with pytest.raises(RuntimeError, match="reopen and validate"):
        ledger.append("second", "test", {}, "H", keys.private_key)
    assert not ledger.wait_finality("first")

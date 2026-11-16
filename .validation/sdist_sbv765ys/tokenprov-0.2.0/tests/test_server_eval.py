import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from tokenprov.server_eval.config import AuditSettings, Config, Endpoint, Workload, load_config
from tokenprov.server_eval.metrics import summarize
from tokenprov.server_eval.runner import run
from tokenprov.server_eval.security import mutate, oracle_violation, signatures_valid, ATTACKS
from tokenprov.audit import audit
from tokenprov.audit.signedlog import audit_signed_log_plus
from tokenprov.workloads import build_honest_protocol


def config(tmp_path, **kwargs):
    return Config(tmp_path / "config.yaml", (Endpoint("P1", "fixture", base_url="http://127.0.0.1:1"),),
        tmp_path / "server_runs", systems=("Vanilla", "SignedLogPlus", "TokenProv-Full"),
        concurrency=(2,), repetitions=2, requests_per_run=3, warmups=1,
        workload=Workload(calls=(3,), input_tokens=(64,), max_output_tokens=(8,)),
        audit=AuditSettings(probability=1, workers=2, queue_capacity=1), **kwargs)


def test_smoke_counts_all_calls_and_keeps_final_data(tmp_path, monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("fixture accessed network"))
    cfg = config(tmp_path)
    sentinel = tmp_path / "final" / "paper.csv"
    sentinel.parent.mkdir(); sentinel.write_bytes(b"retained paper results")
    root, summary = run(cfg, fixture=True, progress=lambda _: None)
    assert summary["validation"]["passed"]
    assert summary["counts"]["measured_requests"] == 18
    assert summary["counts"]["warmup_requests"] == 6
    assert summary["counts"]["proof_jobs"] == 18  # 2 runs * 3 requests * 3 calls, TP only
    assert summary["proof_queue"]["peak_active_workers"] <= 2
    assert summary["proof_queue"]["peak_queued_jobs"] <= 1
    assert all(x["p99_online_ms"] is None for x in summary["per_repetition"])
    assert all(x["p95_online_ms"] is None for x in summary["per_repetition"])
    calls = [json.loads(x) for x in (root / "calls.jsonl").read_text().splitlines()]
    assert all(x["actual_output_tokens"] == 4 and x["max_output_tokens"] == 8 for x in calls)
    assert not list((root / "witnesses").glob("*.json"))  # No second persistent witness copy.
    references = [json.loads(x) for x in (root / "witnesses.jsonl").read_text().splitlines()]
    assert references and all(x["storage"] == "evidence_bundle_reference" for x in references)
    assert all((root / x["file"]).is_file() for x in references)
    assert sentinel.read_bytes() == b"retained paper results"
    assert summarize(root)["counts"] == summary["counts"]
    another, _ = run(replace(cfg, systems=("Vanilla",), repetitions=1, warmups=0), fixture=True, progress=lambda _: None)
    assert root != another


def test_missing_proof_rows_fail_validation(tmp_path):
    root, _ = run(replace(config(tmp_path), systems=("TokenProv-Full",), repetitions=1, warmups=0),
                  fixture=True, progress=lambda _: None)
    lines = (root / "proofs.jsonl").read_text().splitlines()
    (root / "proofs.jsonl").write_text("\n".join(lines[:-1]) + "\n")
    assert not summarize(root)["validation"]["passed"]


def test_config_rejects_secrets_unknown_fields_and_wrong_output(tmp_path):
    path = tmp_path / "config.yaml"
    base = {"schema_version": 1, "output_root": "server_runs",
            "endpoints": [{"id": "a", "model": "deployed", "base_url": "http://localhost:8000/v1"}]}
    path.write_text(yaml.safe_dump(base))
    assert load_config(path).endpoints[0].model == "deployed"
    for mutation in (
        {**base, "output_root": "results/final"},
        {**base, "audit": {"probability": -0.1}},
        {**base, "audit": {"workers": True}},
        {**base, "endpoints": [{**base["endpoints"][0], "api_key": "MUST_NOT_BE_IN_CONFIG"}]},
        {**base, "endpoints": [{**base["endpoints"][0], "base_url": "http://user:secret@localhost/"}]},
    ):
        path.write_text(yaml.safe_dump(mutation))
        with pytest.raises(ValueError): load_config(path)


@pytest.mark.parametrize("attack", ATTACKS)
def test_resigned_attacks_have_valid_signatures_and_independent_oracle(attack):
    fixture = build_honest_protocol(calls=3)
    assert audit_signed_log_plus(fixture.authorization, fixture.public_transcript, fixture.private_witness).accepted
    public, private, _ = mutate(fixture, attack)
    assert signatures_valid(fixture, public, private)
    assert oracle_violation(fixture, public, private)
    assert not audit(fixture.authorization, public, private).accepted
    assert not audit_signed_log_plus(fixture.authorization, public, private).accepted


def test_long_lifecycle_does_not_hit_recursion_limit():
    fixture = build_honest_protocol(calls=2, lifecycle_events=1500)
    assert audit(fixture.authorization, fixture.public_transcript, fixture.private_witness).accepted


def test_two_providers_have_distinct_registered_identities(tmp_path):
    from tokenprov.broker import NativeTokenProvBroker
    from tokenprov.common.models import ServiceConfiguration, DeploymentDescriptor
    from tokenprov.harness import EvaluationHarness
    configs = [ServiceConfiguration(p, m, None, {}, {}, {}, "local-research-v1")
               for p, m in (("provider_a", "model_a"), ("provider_b", "model_b"))]
    def forward(call):
        return {"model": call.configuration.model_id, "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 2}}
    descriptors = {c.provider_id: DeploymentDescriptor(c.model_id, "v1", c.model_id+"-hash", "tok", "canon", "serving") for c in configs}
    harness = EvaluationHarness(NativeTokenProvBroker(configs, {c.provider_id: forward for c in configs}), tmp_path / "mixed",
        model_reference="mixed", model_revision="v1", model_hash="hash", tokenizer_hash="tok",
        provider_descriptors=descriptors)
    result = harness.execute("mixed-request", ({"role": "user", "content": "test"},),
        calls_per_request=4, variant="SignedLogPlus")
    assert result.accepted
    assert [r["provider_id"] for r in result.call_records] == ["provider_a", "provider_b"] * 2


def test_http_transport_supports_v1_path_and_hides_credentials(monkeypatch):
    from tokenprov.server_eval.transport import JsonHttpClient
    import urllib.error
    import urllib.request
    captured = []
    def fail(request, **kwargs):
        captured.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 401, "secret message", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", fail)
    with pytest.raises(RuntimeError) as exc:
        JsonHttpClient("http://localhost:8000/v1", "TOP_SECRET").request("/v1/models")
    assert captured == ["http://localhost:8000/v1/models"]
    assert "TOP_SECRET" not in str(exc.value) and "secret message" not in str(exc.value)


def test_proof_storage_failure_drains_queue_and_invalidates_run(tmp_path):
    from tokenprov.server_eval.artifacts import RunArtifacts
    from tokenprov.server_eval.audit_jobs import AuditQueue, FixtureProofBackend
    from tokenprov.common.serialization import commitment
    cfg = config(tmp_path)
    artifacts = RunArtifacts(cfg, "fixture")
    append = artifacts.append
    def fail_proof_row(name, value):
        if name == "proofs.jsonl": raise OSError("test storage failure")
        return append(name, value)
    artifacts.append = fail_proof_row
    queue = AuditQueue(artifacts, cfg.audit, backend=FixtureProofBackend())
    item = {"request_id": "r", "call_id": "c", "witness": {}, "opening": "nonce",
        "commitment": commitment("provider-witness/v1", {}, "nonce")}
    assert queue.submit_request({"request_id": "r"}, [item], selected=True, settled_ns=0) == 1
    with pytest.raises(RuntimeError, match="persisted"): queue.close()
    assert queue.stats()["storage_errors"] == 1
    assert all(not thread.is_alive() for thread in queue.threads)


def test_protocol_scaling_has_isolated_output_and_scope(tmp_path):
    from tokenprov.server_eval.protocol import run_protocol
    root, count = run_protocol(config(tmp_path), (1, 3), (4,), 1, lambda _: None)
    assert count == 2
    rows = [json.loads(x) for x in (root / "protocol.jsonl").read_text().splitlines()]
    assert [r["lifecycle_events"] for r in rows] == [4, 12]
    assert all(r["success"] and r["measurement_type"] == "PROTOCOL_MICROBENCHMARK" for r in rows)

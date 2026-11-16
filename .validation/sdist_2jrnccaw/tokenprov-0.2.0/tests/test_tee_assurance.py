import copy
import time
from dataclasses import replace

import pytest

from tokenprov.assurance.hardware import AttestationError, parse_tdx_v4
from tokenprov.assurance.tee import TeeClient, TeePolicy, TeeVerifier, cpu_report_data
from tokenprov.assurance.tee_runtime import TeeRuntime, program_hash
from tokenprov.common.models import ProviderCallRequest, ServiceConfiguration
from tokenprov.common.serialization import digest, primitive
from tokenprov.harness import EvaluationHarness
from tokenprov.broker import NativeTokenProvBroker
from tokenprov.server_eval.model_artifacts import model_manifest


@pytest.fixture
def tee(tmp_path, monkeypatch):
    # Test-only model bytes and test-only hardware verifier replacements. The
    # production runtime has no mock/skip-attestation mode.
    model = tmp_path / "model"; model.mkdir()
    (model / "model.safetensors").write_bytes(b"UNIT_TEST_NOT_MODEL_WEIGHTS")
    (model / "tokenizer.json").write_text("{}")
    manifest = model_manifest(model)
    class Backend:
        def infer(self, payload):
            return {"model": "test-model", "choices": [{"message": {"content": "verified output"}}],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 2}}
    runtime = TeeRuntime({"provider_id": "P1", "model": "test-model", "model_path": str(model),
        "revision": "v1", "model_hash": manifest["model_hash"], "tokenizer_hash": manifest["tokenizer_hash"]}, Backend())
    policy = TeePolicy(("a"*64,), (program_hash(),))
    verifier = TeeVerifier(policy)
    challenge = verifier.challenge()
    now = int(time.time())
    session = {"version": 1, "session_id": "test-session", "challenge": challenge,
        "runtime_public_key": runtime.keys.public_key, "deployment_digest": runtime.deployment_digest,
        "program_hash": program_hash(), "issued_at": now-1, "expires_at": now+300,
        "gpu_token": {"unit_test": True}, "cpu_evidence": {"unit_test": True}}
    def cpu(evidence, report_data):
        return {"verified": True, "platform": "tdx", "measurement": "a"*64,
                "report_data": report_data, "debug": False, "tcb_status": "UpToDate"}
    monkeypatch.setattr("tokenprov.assurance.tee.verify_tdx_quote", cpu)
    monkeypatch.setattr(verifier.gpu, "verify", lambda *args: [{"unit_test": True}])
    monkeypatch.setattr(verifier, "_verify_workload", lambda *args: {"scope": "UNIT_TEST_ONLY"})
    verifier.enroll(session, challenge, runtime.deployment_digest)
    runtime.sessions[session["session_id"]] = session
    client = TeeClient.__new__(TeeClient)
    client.verifier, client.session, client.descriptor = verifier, session, runtime.descriptor
    client.deployment_digest = runtime.deployment_digest
    class Http:
        def request(self, path, payload):
            assert path == "/execute"
            return runtime.execute(primitive(payload))
    client.http = Http()
    return runtime, client


def test_tee_path_executes_and_verifies_receipt_bound_transition(tee, tmp_path):
    runtime, client = tee
    cfg = ServiceConfiguration("P1", "test-model", "http://localhost:18443",
        {"temperature": 0, "max_tokens": 8, "chat_template_kwargs": {"enable_thinking": False}},
        {"cross_call_kv": "disabled"}, {"source": "local-generated-token-ids"}, "local-research-v1")
    def no_remote_backend(call): pytest.fail("TEE fell back to ordinary model endpoint")
    harness = EvaluationHarness(NativeTokenProvBroker([cfg], {"P1": no_remote_backend}), tmp_path / "run",
        model_reference="test-model", model_revision="v1", model_hash=runtime.manifest["model_hash"],
        tokenizer_hash=runtime.manifest["tokenizer_hash"], provider_descriptors={"P1": runtime.descriptor}, tee_client=client)
    result = harness.execute("request", ({"role": "user", "content": "test"},), variant="TokenProv-TEE", calls_per_request=2)
    assert result.accepted and result.actual_calls == 2
    assert len(client.verifier.verified) == 2
    assert result.proof_inputs[0]["receipt"]["assurance_mode"] == "tee"


def invocation(runtime, call_id="c-0", auth="auth"):
    cfg = ServiceConfiguration("P1", "test-model", "http://localhost",
        {"temperature": 0, "max_tokens": 8}, {"cross_call_kv": "disabled"},
        {"source": "local-generated-token-ids"}, "local-research-v1")
    call = ProviderCallRequest("request", call_id, ({"role": "user", "content": "test"},), cfg,
        {"kind": "identity"}, "hb")
    context = {"authorization_digest": auth, "deployment_id": "dep", "hb_handoff_hash": "hb", "bp_handoff_hash": "bp",
               "descriptor": runtime.descriptor, "price_policy": runtime.price}
    return call, context


@pytest.mark.parametrize("field,value", [("output_hash", "tampered"), ("input_hash", "tampered"),
    ("authorization_digest", "tampered"), ("charge_microunits", 999)])
def test_tampered_tee_result_is_rejected(tee, field, value):
    runtime, client = tee
    original = client.http.request
    def changed(path, payload):
        response = copy.deepcopy(original(path, payload))
        response["shared"][field] = value
        return response
    client.http.request = changed
    call, context = invocation(runtime)
    with pytest.raises(AttestationError): client.execute(call, context)


def test_tee_conflicting_call_is_not_resigned(tee):
    runtime, client = tee
    call, context = invocation(runtime)
    client.execute(call, context)
    with pytest.raises(AttestationError):
        client.execute(replace(call, transformed_messages=({"role": "user", "content": "changed"},)), context)


def test_tee_state_jump_and_receipt_anchor_change_rejected(tee):
    runtime, client = tee
    call, context = invocation(runtime)
    value = client.execute(call, context)
    bad = copy.deepcopy(value["artifact"])
    bad["transition"]["sequence"] = 99
    assert bad != value["artifact"]
    # Replacing the signature-bound transition cannot be repaired by the host.
    from tokenprov.crypto.signing import verify_object
    assert not verify_object(client.session["runtime_public_key"], "tee-transition/v1", bad["transition"], bad["signature"])


def test_tee_enrollment_rejects_unsolicited_or_replayed_challenge(tee):
    runtime, client = tee
    with pytest.raises(AttestationError):
        client.verifier.enroll(client.session, client.session["challenge"], runtime.deployment_digest)
    changed = {**client.session, "challenge": "0"*64}
    with pytest.raises(AttestationError): client.verifier.enroll(changed, "0"*64, runtime.deployment_digest)


def test_tee_requires_actual_backend_and_pinned_policy(tmp_path):
    from tokenprov.assurance.tee import TeePolicy
    path = tmp_path / "policy.yaml"
    path.write_text("allowed_measurements: []\nallowed_program_hashes: []\n")
    with pytest.raises(ValueError): TeePolicy.load(path)


def test_tdx_parser_rejects_other_formats_and_reads_bound_fields():
    with pytest.raises(AttestationError): parse_tdx_v4(b"fake quote")
    data = bytearray(636)
    data[:2] = (4).to_bytes(2, "little"); data[4:8] = (0x81).to_bytes(4, "little")
    data[568:632] = bytes(range(64))
    assert parse_tdx_v4(data)["report_data"] == bytes(range(64)).hex()
    data[168] = 1
    assert parse_tdx_v4(data)["debug"] is True


def test_hardware_quote_alone_cannot_enroll_full_tee(tee):
    runtime, client = tee
    with pytest.raises(AttestationError, match="CPU_IMAGE_NOT_PROVISIONED"):
        TeeVerifier._verify_workload(client.verifier, client.session, {}, [])


def test_relying_party_session_deadline_is_enforced(tee):
    runtime, client = tee
    call, context = invocation(runtime)
    client.verifier.sessions[client.session["session_id"]]["deadline_monotonic"] = 0
    with pytest.raises(AttestationError): client.execute(call, context)


@pytest.mark.parametrize("name", ["pytorch_model.bin", "adapter_config.json", "modeling_custom.py"])
def test_tee_manifest_rejects_alternate_artifact_loading(tmp_path, name):
    (tmp_path / "model.safetensors").write_bytes(b"test")
    (tmp_path / "tokenizer.json").write_text("{}")
    (tmp_path / name).write_bytes(b"test")
    with pytest.raises(ValueError): model_manifest(tmp_path, strict=True)


def test_workload_adapter_response_must_bind_approved_policy_and_invocation(tee, tmp_path, monkeypatch):
    runtime, client = tee
    policy = tmp_path / "approved.json"; policy.write_text('{"profile":"UNIT_TEST_ONLY"}')
    verifier = TeeVerifier(replace(client.verifier.policy, workload_verifier_command=("test-verifier",),
        workload_policy_file=str(policy)))
    def response(command, request, timeout):
        return {"verified": True, "binding": request["binding"], "expected": request["expected"],
            "workload_identity_verified": True, "immutable_artifacts_verified": True,
            "gpu_device_binding_verified": True, "secure_cpu_gpu_path_verified": True}
    monkeypatch.setattr("tokenprov.assurance.tee.command_json", response)
    assert verifier._verify_workload(client.session, {}, [])["verified"]
    def wrong_binding(command, request, timeout):
        return {**response(command, request, timeout), "expected": {}}
    monkeypatch.setattr("tokenprov.assurance.tee.command_json", wrong_binding)
    with pytest.raises(AttestationError): verifier._verify_workload(client.session, {}, [])

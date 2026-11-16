from tokenprov.broker import NativeTokenProvBroker
from tokenprov.common.models import ServiceConfiguration
from tokenprov.harness import EvaluationHarness


def broker():
    config = ServiceConfiguration("P1", "deterministic", None, {}, {}, {},
                                  "local-research-v1")
    def forward(call):
        return {"model": "deterministic", "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 1}}
    return NativeTokenProvBroker([config], {"P1": forward})


def test_variants_share_broker_path(tmp_path):
    messages = ({"role": "user", "content": "test"},)
    for variant in ("Vanilla", "SignedLog", "FullTrace", "TokenProv-Full"):
        harness = EvaluationHarness(broker(), tmp_path / variant,
            model_reference="deterministic", model_revision="v1",
            model_hash="model-hash", tokenizer_hash="tokenizer-hash")
        result = harness.execute(f"req-{variant}", messages, variant=variant,
                                 calls_per_request=2, lifecycle_events=8)
        assert result.accepted
        assert result.actual_calls == 2
        assert result.timings_ns["T_online"] > 0
        assert result.protocol_bytes == 0 if variant == "Vanilla" else result.protocol_bytes > 0
        if variant in {"FullTrace", "TokenProv-Full"}:
            assert result.lifecycle_events == 8
            assert result.timings_ns["T_audit"] > 0

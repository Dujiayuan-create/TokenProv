from tokenprov.broker import NativeTokenProvBroker
from tokenprov.common.models import ServiceConfiguration
from tokenprov.harness import SingleCallHarness


def test_single_call_harness_separates_clocks_and_audits(tmp_path):
    config = ServiceConfiguration("P1", "fixture-model", None, {}, {}, {}, "local-research-v1")
    raw = {"model": "fixture-model",
           "choices": [{"message": {"content": "fixture answer"}, "finish_reason": "stop"}],
           "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}
    broker = NativeTokenProvBroker([config], {"P1": lambda call: raw})
    harness = SingleCallHarness(broker, tmp_path, model_reference="fixture-model",
                                model_revision="fixture-revision")
    result = harness.execute("r-e2e", ({"role": "user", "content": "hello"},))
    assert result.accepted and result.text == "fixture answer"
    assert result.usage.n_pf == 3 and result.usage.n_out == 2
    assert set(result.timings_ns) == {"T_online", "T_settlement", "T_evidence",
                                      "T_ledger", "T_audit"}
    assert all(value > 0 for value in result.timings_ns.values())

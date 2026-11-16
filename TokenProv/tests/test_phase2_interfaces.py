import pytest

from tokenprov.audit import audit
from tokenprov.broker import (BrokerUnavailable, LiteLLMBrokerAdapter,
                              LiteLLMProxyAdapter, NativeTokenProvBroker)
from tokenprov.common.models import CallIntent, ServiceConfiguration
from tokenprov.provider import DeterministicProvider, VLLMProviderAdapter
from tokenprov.workloads import build_honest_protocol


def configuration(provider_id="P1"):
    return ServiceConfiguration(provider_id, "deterministic/tokenprov", None,
                                {"temperature": 0}, {"mode": "disabled"},
                                {"tokenizer": "deterministic"}, "price-v1")


def test_native_broker_transform_route_and_deterministic_provider():
    provider = DeterministicProvider()
    broker = NativeTokenProvBroker([configuration()], {"P1": provider.infer})
    intent = CallIntent("r-1", "c-1", ({"role": "user", "content": "hello"},), {})
    call = broker.make_call(intent, "hb-hash", {"kind": "prepend_system", "content": "safe"})
    result = broker.forward(call)
    assert call.configuration.provider_id == "P1"
    assert call.transformed_messages[0] == {"role": "system", "content": "safe"}
    assert result.text.startswith("deterministic:") and result.usage.n_pf > 0
    assert result.raw["model_execution"] is False


def test_native_broker_routing_and_unavailable_forwarder():
    broker = NativeTokenProvBroker([configuration("P1"), configuration("P2")])
    intent = CallIntent("r-1", "c-1", (), {})
    assert broker.route(intent) == "P1" and broker.route(intent) == "P2"
    with pytest.raises(BrokerUnavailable):
        broker.forward(broker.make_call(intent, "hb", provider_hint="P1"))


def test_optional_adapters_report_actual_availability():
    available, reason = LiteLLMBrokerAdapter.availability()
    assert isinstance(available, bool) and isinstance(reason, str) and reason
    healthy, detail = VLLMProviderAdapter("http://127.0.0.1:9", timeout_s=.05).health()
    assert healthy is False and detail
    proxy = LiteLLMProxyAdapter([configuration()], "http://127.0.0.1:9", timeout_s=.05)
    healthy, detail = proxy.health()
    assert healthy is False and detail


def test_packaged_eight_call_fixture_audits_successfully():
    fixture = build_honest_protocol(calls=8)
    result = audit(fixture.authorization, fixture.public_transcript, fixture.private_witness)
    assert result.accepted

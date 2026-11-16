"""Broker interface and dependency-free TokenProv-native implementation."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from typing import Any, Callable

from tokenprov.common.models import CallIntent, ProviderCallRequest, ServiceConfiguration


class BrokerUnavailable(RuntimeError):
    pass


class Broker(ABC):
    @abstractmethod
    def transform(self, intent: CallIntent, rule: dict[str, Any]) -> tuple[dict[str, Any], ...]: ...

    @abstractmethod
    def route(self, intent: CallIntent, provider_hint: str | None = None) -> str: ...

    @abstractmethod
    def select_configuration(self, provider_id: str) -> ServiceConfiguration: ...

    @abstractmethod
    def forward(self, call: ProviderCallRequest): ...


class NativeTokenProvBroker(Broker):
    """Deterministic routing/transform layer used before optional LiteLLM."""

    def __init__(self, configurations: list[ServiceConfiguration],
                 forwarders: dict[str, Callable[[ProviderCallRequest], Any]] | None = None):
        if not configurations:
            raise ValueError("at least one provider configuration is required")
        self._configurations = {item.provider_id: item for item in configurations}
        if len(self._configurations) != len(configurations):
            raise ValueError("provider ids must be unique")
        self._order = tuple(self._configurations)
        self._forwarders = dict(forwarders or {})
        self._next = 0

    @property
    def configurations(self) -> tuple[ServiceConfiguration, ...]:
        return tuple(self._configurations.values())

    def transform(self, intent: CallIntent, rule: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        kind = rule.get("kind", "identity")
        messages = list(intent.structured_messages)
        if kind == "identity":
            return tuple(dict(message) for message in messages)
        if kind == "prepend_system":
            content = rule.get("content")
            if not isinstance(content, str):
                raise ValueError("prepend_system requires string content")
            return ({"role": "system", "content": content},
                    *(dict(message) for message in messages))
        raise ValueError(f"unsupported deterministic transformation: {kind}")

    def route(self, intent: CallIntent, provider_hint: str | None = None) -> str:
        del intent
        if provider_hint is not None:
            if provider_hint not in self._configurations:
                raise BrokerUnavailable(f"unknown provider: {provider_hint}")
            return provider_hint
        selected = self._order[self._next % len(self._order)]
        self._next += 1
        return selected

    def select_configuration(self, provider_id: str) -> ServiceConfiguration:
        try:
            return self._configurations[provider_id]
        except KeyError as exc:
            raise BrokerUnavailable(f"unknown provider: {provider_id}") from exc

    def make_call(self, intent: CallIntent, hb_handoff_hash: str,
                  rule: dict[str, Any] | None = None,
                  provider_hint: str | None = None) -> ProviderCallRequest:
        applied_rule = dict(rule or {"kind": "identity", "version": "v1"})
        provider_id = self.route(intent, provider_hint)
        return ProviderCallRequest(intent.request_id, intent.call_id,
                                   self.transform(intent, applied_rule),
                                   self.select_configuration(provider_id),
                                   applied_rule, hb_handoff_hash)

    def forward(self, call: ProviderCallRequest):
        forwarder = self._forwarders.get(call.configuration.provider_id)
        if forwarder is None:
            raise BrokerUnavailable(f"provider {call.configuration.provider_id} has no forwarder")
        return forwarder(call)

    def with_forwarder(self, provider_id: str, forwarder: Callable[[ProviderCallRequest], Any]):
        if provider_id not in self._configurations:
            raise BrokerUnavailable(f"unknown provider: {provider_id}")
        self._forwarders[provider_id] = forwarder

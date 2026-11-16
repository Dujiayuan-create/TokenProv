"""Optional LiteLLM SDK adapter; importing TokenProv does not require LiteLLM."""
from __future__ import annotations

import importlib.util
import json
import urllib.error
import urllib.request

from tokenprov.common.models import ProviderCallRequest

from .base import BrokerUnavailable, NativeTokenProvBroker


class LiteLLMBrokerAdapter(NativeTokenProvBroker):
    @staticmethod
    def availability() -> tuple[bool, str]:
        if importlib.util.find_spec("litellm") is None:
            return False, "LiteLLM is not installed in the active environment"
        return True, "LiteLLM import is available"

    def forward(self, call: ProviderCallRequest):
        available, reason = self.availability()
        if not available:
            raise BrokerUnavailable(reason)
        import litellm
        config = call.configuration
        kwargs = dict(config.decoding_config)
        if config.endpoint:
            kwargs["api_base"] = config.endpoint
        return litellm.completion(model=config.model_id,
                                  messages=list(call.transformed_messages), **kwargs)


class LiteLLMProxyAdapter(NativeTokenProvBroker):
    """HTTP adapter for a separately managed LiteLLM Proxy process."""

    def __init__(self, configurations, endpoint: str, api_key: str | None = None,
                 timeout_s: float = 30.0):
        super().__init__(configurations)
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s

    def health(self) -> tuple[bool, str]:
        try:
            with urllib.request.urlopen(self.endpoint + "/health/readiness",
                                        timeout=min(self.timeout_s, 3.0)) as response:
                return 200 <= response.status < 300, f"HTTP {response.status}"
        except (OSError, urllib.error.URLError) as exc:
            return False, str(exc)

    def forward(self, call: ProviderCallRequest):
        payload = {"model": call.configuration.model_id,
                   "messages": list(call.transformed_messages),
                   **call.configuration.decoding_config}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = urllib.request.Request(self.endpoint + "/v1/chat/completions",
                                         data=json.dumps(payload).encode("utf-8"),
                                         headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                return json.loads(response.read())
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise BrokerUnavailable(f"LiteLLM Proxy unavailable: {exc}") from exc

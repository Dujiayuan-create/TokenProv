"""vLLM OpenAI-compatible HTTP adapter with explicit availability checks."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from tokenprov.common.models import ProviderCallRequest, UsageVector
from tokenprov.common.serialization import canonical_bytes

from .base import InferenceResult, ModelProvider, ProviderUnavailable


class VLLMProviderAdapter(ModelProvider):
    def __init__(self, endpoint: str, timeout_s: float = 30.0):
        self.endpoint = endpoint.rstrip("/")
        self.timeout_s = timeout_s

    def canonicalize(self, messages):
        return canonical_bytes(messages)

    def tokenize(self, canonical_input: bytes):
        raise ProviderUnavailable("tokenization is performed and metered by the registered vLLM deployment")

    def health(self) -> tuple[bool, str]:
        try:
            with urllib.request.urlopen(self.endpoint + "/health", timeout=min(self.timeout_s, 3.0)) as response:
                return 200 <= response.status < 300, f"HTTP {response.status}"
        except (OSError, urllib.error.URLError) as exc:
            return False, str(exc)

    def infer(self, call: ProviderCallRequest) -> InferenceResult:
        payload = {"model": call.configuration.model_id,
                   "messages": list(call.transformed_messages),
                   **call.configuration.decoding_config}
        request = urllib.request.Request(
            self.endpoint + "/v1/chat/completions",
            data=canonical_bytes(payload), headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                raw = json.loads(response.read())
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise ProviderUnavailable(f"vLLM endpoint unavailable: {exc}") from exc
        try:
            usage_raw = raw["usage"]
            usage = UsageVector(n_pf=int(usage_raw["prompt_tokens"]),
                                n_out=int(usage_raw["completion_tokens"]))
            text = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderUnavailable("vLLM response lacks required output/usage fields") from exc
        return InferenceResult(text, usage, raw)

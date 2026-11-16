"""Protocol-only deterministic provider; never an LLM latency baseline."""
from __future__ import annotations

import re

from tokenprov.common.models import ProviderCallRequest, UsageVector
from tokenprov.common.serialization import canonical_bytes, digest

from .base import InferenceResult, ModelProvider


class DeterministicProvider(ModelProvider):
    measurement_type = "PROTOCOL_MICROBENCHMARK"

    def canonicalize(self, messages):
        return canonical_bytes(messages)

    def tokenize(self, canonical_input: bytes) -> tuple[str, ...]:
        return tuple(re.findall(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]", canonical_input.decode("utf-8")))

    def infer(self, call: ProviderCallRequest) -> InferenceResult:
        canonical = self.canonicalize(call.transformed_messages)
        input_tokens = self.tokenize(canonical)
        output = "deterministic:" + digest("deterministic-provider-output/v1", {
            "request_id": call.request_id,
            "call_id": call.call_id,
            "input": canonical,
            "model": call.configuration.model_id,
        })[:24]
        output_tokens = self.tokenize(output.encode("utf-8"))
        usage = UsageVector(n_pf=len(input_tokens), n_out=len(output_tokens))
        return InferenceResult(output, usage, {"backend": "deterministic", "model_execution": False})

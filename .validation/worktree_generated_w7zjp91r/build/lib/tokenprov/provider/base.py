"""Model-provider interface shared by deterministic and real serving adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from tokenprov.common.models import ProviderCallRequest, UsageVector


class ProviderUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class InferenceResult:
    text: str
    usage: UsageVector
    raw: dict[str, Any]


class ModelProvider(ABC):
    @abstractmethod
    def canonicalize(self, messages: tuple[dict[str, Any], ...]) -> bytes: ...

    @abstractmethod
    def tokenize(self, canonical_input: bytes) -> tuple[str, ...]: ...

    @abstractmethod
    def infer(self, call: ProviderCallRequest) -> InferenceResult: ...

    def meter(self, result: InferenceResult) -> UsageVector:
        return result.usage

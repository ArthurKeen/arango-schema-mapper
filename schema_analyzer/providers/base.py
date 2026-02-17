from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class LLMResponse:
    text: str
    raw: Any | None = None


class LLMProvider(Protocol):
    def generate(self, *, model: str, system: str, prompt: str, timeout_ms: int) -> LLMResponse: ...


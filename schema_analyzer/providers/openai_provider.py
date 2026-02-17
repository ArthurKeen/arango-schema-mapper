from __future__ import annotations

from dataclasses import dataclass

from ..errors import SchemaAnalyzerError
from .base import LLMResponse


@dataclass
class OpenAIProvider:
    api_key: str

    def generate(self, *, model: str, system: str, prompt: str, timeout_ms: int) -> LLMResponse:
        try:
            from openai import OpenAI  # type: ignore
        except Exception as e:  # pragma: no cover
            raise SchemaAnalyzerError(
                "OpenAI SDK not installed. Install extra: pip install -e '.[openai]'",
                code="PROVIDER_MISSING",
                cause=e,
            )

        client = OpenAI(api_key=self.api_key)
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                timeout=timeout_ms / 1000.0,
            )
        except Exception as e:  # pragma: no cover
            raise SchemaAnalyzerError("OpenAI request failed", code="PROVIDER_ERROR", cause=e)

        text = resp.choices[0].message.content or ""
        return LLMResponse(text=text, raw=resp)


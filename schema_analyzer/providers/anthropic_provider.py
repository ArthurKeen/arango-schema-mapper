from __future__ import annotations

from dataclasses import dataclass

from ..errors import SchemaAnalyzerError
from .base import LLMResponse


@dataclass
class AnthropicProvider:
    api_key: str

    def generate(self, *, model: str, system: str, prompt: str, timeout_ms: int) -> LLMResponse:
        try:
            import anthropic  # type: ignore
        except Exception as e:  # pragma: no cover
            raise SchemaAnalyzerError(
                "Anthropic SDK not installed. Install extra: pip install -e '.[anthropic]'",
                code="PROVIDER_MISSING",
                cause=e,
            )

        client = anthropic.Anthropic(api_key=self.api_key)
        try:
            resp = client.messages.create(
                model=model,
                system=system,
                max_tokens=4096,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
                timeout=timeout_ms / 1000.0,
            )
        except Exception as e:  # pragma: no cover
            raise SchemaAnalyzerError("Anthropic request failed", code="PROVIDER_ERROR", cause=e)

        text = ""
        # Anthropic response content is a list of blocks.
        try:
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    text += block.text
        except Exception:
            text = str(resp)

        return LLMResponse(text=text, raw=resp)


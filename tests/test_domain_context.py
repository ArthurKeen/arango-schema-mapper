"""Tests for caller-supplied domainContext (PRD §4.7)."""

from __future__ import annotations

import json

from schema_analyzer.analyzer import AgenticSchemaAnalyzer
from schema_analyzer.domain_detect import domain_hint_from_context


def test_hint_from_string():
    h = domain_hint_from_context("healthcare")
    assert h is not None
    assert h.domain == "healthcare"
    assert h.confidence == 1.0
    assert "caller-provided" in h.matched_signals


def test_hint_from_dict_with_spec():
    h = domain_hint_from_context(
        {
            "domain": "insurance",
            "description": "policies and claims",
            "entities": [{"name": "Policy"}, {"name": "Claim"}],
            "relationships": [{"type": "FILED"}],
        }
    )
    assert h.domain == "insurance"
    ctx = h.prompt_context()
    assert "insurance" in ctx
    assert "Policy" in ctx and "Claim" in ctx  # spec vocabulary block
    assert "FILED" in ctx


def test_hint_from_empty_is_none():
    assert domain_hint_from_context(None) is None
    assert domain_hint_from_context("") is None
    assert domain_hint_from_context({}) is None


class _FakeProvider:
    def __init__(self, text):
        self._text = text
        self.last_prompt = ""

    def generate(self, *, model, system, prompt, timeout_ms):
        self.last_prompt = prompt

        class R:
            text = self._text

        return R()


class _FakeDB:
    def collections(self):
        class C:
            def __init__(self, t):
                self._t = t

            def properties(self):
                return {"type": self._t}

            def count(self):
                return 0

            def indexes(self):
                return []

        return {"users": C(2)}

    def graphs(self):
        return []


_VALID = json.dumps(
    {
        "conceptualSchema": {"entities": [], "relationships": [], "properties": []},
        "physicalMapping": {"entities": {}, "relationships": {}},
        "metadata": {
            "confidence": 0.8,
            "timestamp": "t",
            "analyzedCollectionCounts": {"documentCollections": 1, "edgeCollections": 0},
            "detectedPatterns": [],
        },
    }
)


def test_domain_context_overrides_detection_and_reaches_prompt(monkeypatch):
    provider = _FakeProvider(_VALID)
    import schema_analyzer.analyzer as am

    monkeypatch.setattr(am, "create_provider", lambda name, *, api_key: provider)

    analyzer = AgenticSchemaAnalyzer(
        llm_provider="openai",
        api_key="k",
        model="m",
        domain_context={"domain": "aerospace", "description": "rockets and payloads"},
    )
    res = analyzer.analyze_physical_schema(_FakeDB(), use_cache=False)
    assert "aerospace" in provider.last_prompt
    assert res.metadata.detected_domain == "aerospace"
    assert res.metadata.detected_domain_confidence == 1.0

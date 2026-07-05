"""Tests for field-name masking with output round-tripping (PRD §4.3)."""

from __future__ import annotations

import json
import re

from schema_analyzer.analyzer import AgenticSchemaAnalyzer
from schema_analyzer.redaction import (
    RedactionOptions,
    build_field_name_map,
    redact_snapshot_for_egress,
    unmask_field_names,
)

_TOKEN_RE = re.compile(r"redacted_field_\d+")


def _snapshot():
    return {
        "collections": [
            {
                "name": "users",
                "type": "document",
                "candidate_type_fields": ["kind"],
                "sample_field_value_counts": {"kind": [{"value": "admin", "count": 3}]},
                "observed_fields": {"fields": ["email", "ssn", "kind"]},
                "indexes": [{"type": "persistent", "fields": ["email", "_key"]}],
                "sample_documents": [{"email": "a@b.c", "ssn": "123", "_key": "u1"}],
            },
            {
                "name": "follows",
                "type": "edge",
                "observed_fields": {"by_type": {"FOLLOWS": ["since", "weight"]}},
            },
        ]
    }


# ── RedactionOptions ──────────────────────────────────────────────────────


def test_options_parse_and_active():
    opts = RedactionOptions.from_dict({"maskFieldNames": True})
    assert opts.mask_field_names is True
    assert opts.active is True
    assert RedactionOptions().active is False


# ── build_field_name_map ──────────────────────────────────────────────────


def test_map_collects_all_names_excludes_system():
    m = build_field_name_map(_snapshot()["collections"])
    # user field names present
    for name in ("email", "ssn", "kind", "since", "weight"):
        assert name in m
    # system fields excluded
    assert "_key" not in m
    # tokens are the name-like sentinel form, deterministic (sorted)
    assert all(v.startswith("redacted_field_") for v in m.values())
    assert m["email"] == "redacted_field_0"  # sorted: email < kind < since < ssn < weight
    assert len(set(m.values())) == len(m)  # bijective


# ── masking ───────────────────────────────────────────────────────────────


def test_masking_replaces_names_everywhere_but_keeps_system_and_collections():
    opts = RedactionOptions(mask_field_names=True)
    red = redact_snapshot_for_egress(_snapshot(), opts)
    dumped = json.dumps(red)

    # real field names gone
    for name in ("email", "ssn", "kind", "since", "weight"):
        assert f'"{name}"' not in dumped
    # collection names preserved (not field names)
    assert "users" in dumped and "follows" in dumped
    # system field preserved in index + sample doc keys
    users = red["collections"][0]
    assert "_key" in users["sample_documents"][0]
    assert "_key" in users["indexes"][0]["fields"]
    # tokens present in each field-bearing location
    assert users["candidate_type_fields"] == ["redacted_field_1"]  # 'kind'
    assert set(users["sample_field_value_counts"].keys()) == {"redacted_field_1"}
    assert all(_TOKEN_RE.fullmatch(f) for f in users["observed_fields"]["fields"] if f != "_key")
    edge = red["collections"][1]
    assert all(_TOKEN_RE.fullmatch(f) for f in edge["observed_fields"]["by_type"]["FOLLOWS"])


def test_original_snapshot_not_mutated():
    snap = _snapshot()
    redact_snapshot_for_egress(snap, RedactionOptions(mask_field_names=True))
    assert snap["collections"][0]["observed_fields"]["fields"] == ["email", "ssn", "kind"]


# ── unmask round-trip ───────────────────────────────────────────────────────


def test_unmask_roundtrip_standalone_and_embedded():
    m = build_field_name_map(_snapshot()["collections"])
    payload = {
        "properties": [{"name": m["ssn"]}],
        "note": f"derived from {m['email']} and {m['kind']}",
        m["weight"]: "a key that is a token",
    }
    out = unmask_field_names(payload, m)
    assert out["properties"][0]["name"] == "ssn"
    assert out["note"] == "derived from email and kind"
    assert "weight" in out  # dict key un-masked
    # no residual tokens anywhere
    assert not _TOKEN_RE.search(json.dumps(out))


def test_unmask_no_map_is_noop():
    assert unmask_field_names({"a": "b"}, {}) == {"a": "b"}


def test_unmask_unknown_token_left_intact():
    out = unmask_field_names({"x": "redacted_field_999"}, {"email": "redacted_field_0"})
    assert out["x"] == "redacted_field_999"


# ── end-to-end through the analyzer (round-trip) ────────────────────────────


class _EchoProvider:
    """Fake LLM that echoes back the masked field tokens it sees in the prompt,
    simulating a model that faithfully carries identifiers through."""

    def __init__(self):
        self.last_prompt = ""

    def generate(self, *, model, system, prompt, timeout_ms):
        self.last_prompt = prompt
        tokens = sorted(set(_TOKEN_RE.findall(prompt)))
        props = [{"name": t, "description": f"the {t} field"} for t in tokens[:3]]
        text = json.dumps(
            {
                "conceptualSchema": {"entities": [], "relationships": [], "properties": props},
                "physicalMapping": {"entities": {}, "relationships": {}},
                "metadata": {
                    "confidence": 0.9,
                    "timestamp": "t",
                    "analyzedCollectionCounts": {"documentCollections": 1, "edgeCollections": 1},
                    "detectedPatterns": [],
                },
            }
        )

        class R:
            pass

        r = R()
        r.text = text
        return r


class _FakeDB:
    def collections(self):
        return []


def test_analyzer_roundtrips_field_names(monkeypatch):
    provider = _EchoProvider()
    import schema_analyzer.analyzer as am

    monkeypatch.setattr(am, "create_provider", lambda name, *, api_key: provider)

    analyzer = AgenticSchemaAnalyzer(
        llm_provider="openai",
        api_key="k",
        model="m",
        redaction=RedactionOptions(mask_field_names=True),
    )
    res = analyzer.analyze_physical_schema(_FakeDB(), use_cache=False, _snapshot=_snapshot())

    # prompt saw tokens, not real field names
    assert "redacted_field_" in provider.last_prompt
    assert '"email"' not in provider.last_prompt and '"ssn"' not in provider.last_prompt

    # result un-masked back to real names; no residual tokens
    dumped = json.dumps(res.model_dump())
    assert not _TOKEN_RE.search(dumped)
    prop_names = {p.get("name") for p in res.conceptual_schema.get("properties", [])}
    assert prop_names & {"email", "kind", "since", "ssn", "weight"}
    assert not any((p.get("name") or "").startswith("redacted_field_") for p in res.conceptual_schema["properties"])

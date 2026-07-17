"""Tests for element-level source provenance (PRD §3.13.2)."""

from __future__ import annotations

from schema_analyzer.provenance import annotate_provenance


def _data(metadata=None):
    return {
        "conceptualSchema": {
            "entities": [{"name": "User"}, {"name": "Audit"}],
            "relationships": [{"type": "WROTE", "fromEntity": "User", "toEntity": "User"}],
            "properties": [],
        },
        "physicalMapping": {
            "entities": {
                "User": {"style": "COLLECTION", "collectionName": "users"},
                "Audit": {"style": "COLLECTION", "collectionName": "audit_log"},
            },
            "relationships": {"WROTE": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "wrote"}},
        },
        "metadata": metadata or {},
    }


def test_provenance_tags_baseline_when_used_baseline():
    data = _data()
    annotate_provenance(data, used_baseline=True)
    assert data["physicalMapping"]["entities"]["User"]["source"] == "baseline"
    assert data["conceptualSchema"]["entities"][0]["source"] == "baseline"
    assert data["conceptualSchema"]["relationships"][0]["source"] == "baseline"


def test_provenance_tags_llm_when_llm_run():
    data = _data()
    annotate_provenance(data, used_baseline=False)
    assert data["physicalMapping"]["entities"]["User"]["source"] == "llm"
    assert data["physicalMapping"]["relationships"]["WROTE"]["source"] == "llm"


def test_provenance_backfilled_collection_is_baseline_even_on_llm_run():
    data = _data(metadata={"reconciliation": {"backfilled_collections": ["audit_log"]}})
    annotate_provenance(data, used_baseline=False)
    # Audit's collection was backfilled -> baseline; User stays llm.
    assert data["physicalMapping"]["entities"]["Audit"]["source"] == "baseline"
    assert data["physicalMapping"]["entities"]["User"]["source"] == "llm"
    # Conceptual entity inherits its mapping's source.
    audit = next(e for e in data["conceptualSchema"]["entities"] if e["name"] == "Audit")
    assert audit["source"] == "baseline"


def test_provenance_preserves_human_tag():
    data = _data()
    data["physicalMapping"]["entities"]["User"]["source"] = "human"
    annotate_provenance(data, used_baseline=False)
    assert data["physicalMapping"]["entities"]["User"]["source"] == "human"


# ── Temporal lineage: firstSeenAt / lastValidatedAt (PRD §3.13.2) ────────


def test_stamp_temporal_provenance_fresh_elements():
    from schema_analyzer.provenance import stamp_temporal_provenance

    data = _data()
    stamp_temporal_provenance(data, now="2026-07-17T00:00:00Z")
    for element in (
        data["conceptualSchema"]["entities"][0],
        data["conceptualSchema"]["relationships"][0],
        data["physicalMapping"]["entities"]["User"],
        data["physicalMapping"]["relationships"]["WROTE"],
    ):
        assert element["firstSeenAt"] == "2026-07-17T00:00:00Z"
        assert element["lastValidatedAt"] == "2026-07-17T00:00:00Z"


def test_stamp_temporal_provenance_revalidation_keeps_first_seen():
    from schema_analyzer.provenance import stamp_temporal_provenance

    data = _data()
    stamp_temporal_provenance(data, now="2026-01-01T00:00:00Z")
    stamp_temporal_provenance(data, now="2026-07-17T00:00:00Z")
    user = data["physicalMapping"]["entities"]["User"]
    assert user["firstSeenAt"] == "2026-01-01T00:00:00Z"
    assert user["lastValidatedAt"] == "2026-07-17T00:00:00Z"


def test_carry_forward_first_seen_matches_by_identity():
    from schema_analyzer.provenance import carry_forward_first_seen, stamp_temporal_provenance

    prior = _data()
    stamp_temporal_provenance(prior, now="2026-01-01T00:00:00Z")

    current = _data()
    # Simulate a schema change: Audit is gone, Post is new.
    current["conceptualSchema"]["entities"] = [{"name": "User"}, {"name": "Post"}]
    current["physicalMapping"]["entities"] = {
        "User": {"style": "COLLECTION", "collectionName": "users"},
        "Post": {"style": "COLLECTION", "collectionName": "posts"},
    }
    stamp_temporal_provenance(current, now="2026-07-17T00:00:00Z")
    carry_forward_first_seen(current, prior)

    user = current["physicalMapping"]["entities"]["User"]
    post = current["physicalMapping"]["entities"]["Post"]
    assert user["firstSeenAt"] == "2026-01-01T00:00:00Z"  # survived the change
    assert user["lastValidatedAt"] == "2026-07-17T00:00:00Z"
    assert post["firstSeenAt"] == "2026-07-17T00:00:00Z"  # genuinely new
    ent_user = next(e for e in current["conceptualSchema"]["entities"] if e["name"] == "User")
    assert ent_user["firstSeenAt"] == "2026-01-01T00:00:00Z"

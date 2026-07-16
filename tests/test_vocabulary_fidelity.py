"""Regression tests for docs/cypher-vocabulary-fidelity-bug-report.md.

Issue #1 — lossy LPG entity labels: ``pascal_case`` collapses separators
(``FIN_METRIC`` → ``FINMETRIC``), so the raw discriminator value must be
recorded as an ``aliases`` entry on the LABEL mapping and survive the export.

Issue #2 — silent top-K entity cap: discriminator values ranked past the
sampling top-K used to vanish without a trace; they must now be reported in
``entityTypeCaps`` / ``relationshipTypeCaps`` and the cap must be raisable
via ``max_entity_types`` / ``sample_value_top_k``.
"""

from schema_analyzer.baseline import (
    infer_baseline_from_snapshot,
    type_value_caps_from_snapshot,
)
from schema_analyzer.exports import build_cypher_resolution_index, export_mapping
from schema_analyzer.redaction import (
    RedactionOptions,
    build_field_name_map,
    redact_snapshot_for_egress,
)
from schema_analyzer.type_detection import _pick_best_type_field


def _lpg_snapshot(**extra_entry_keys):
    entry = {
        "name": "Node",
        "type": "document",
        "count": 100,
        "candidate_type_fields": ["type"],
        "sample_field_value_counts": {
            "type": [
                {"value": "ORG", "count": 60},
                {"value": "FIN_METRIC", "count": 40},
            ]
        },
        **extra_entry_keys,
    }
    return {"version": 2, "collections": [entry], "graphs": []}


# ── Issue #1: raw discriminator value kept as alias ─────────────────────


def test_label_entity_carries_raw_type_value_as_alias():
    out = infer_baseline_from_snapshot(_lpg_snapshot())
    ent = out["physicalMapping"]["entities"]["FINMETRIC"]
    assert ent["typeValue"] == "FIN_METRIC"
    assert ent["aliases"] == ["FIN_METRIC"]


def test_no_alias_when_pascal_case_is_lossless():
    snapshot = _lpg_snapshot(
        count=3,
        sample_field_value_counts={"type": [{"value": "Person", "count": 2}, {"value": "Company", "count": 1}]},
    )
    out = infer_baseline_from_snapshot(snapshot)
    assert "aliases" not in out["physicalMapping"]["entities"]["Person"]
    assert "aliases" not in out["physicalMapping"]["entities"]["Company"]


def test_cypher_export_and_resolution_index_carry_aliases():
    baseline = infer_baseline_from_snapshot(_lpg_snapshot())
    analysis = {
        "conceptualSchema": baseline["conceptualSchema"],
        "physicalMapping": baseline["physicalMapping"],
        "metadata": {},
    }
    exported = export_mapping(analysis, target="cypher")
    assert exported["physicalMapping"]["entities"]["FINMETRIC"]["aliases"] == ["FIN_METRIC"]

    index = build_cypher_resolution_index(analysis)
    assert index["entities"]["FINMETRIC"]["aliases"] == ["FIN_METRIC"]
    # Losslessly-named entities carry no alias noise.
    assert "aliases" not in index["entities"]["ORG"]


# ── Issue #2: entity cap is transparent ─────────────────────────────────


def _capped_snapshot():
    """A type-discriminated collection whose 21st class fell past top-20."""
    top = [{"value": f"CLASS_{i:02d}", "count": 100 - i} for i in range(20)]
    return {
        "version": 2,
        "sample_value_top_k": 20,
        "collections": [
            {
                "name": "Node",
                "type": "document",
                "count": 2000,
                "candidate_type_fields": ["type"],
                "sample_field_value_counts": {"type": top},
                "sample_field_distinct_counts": {"type": 24},
                "sample_field_value_overflow": {
                    "type": [
                        {"value": "CLASS_20", "count": 15},
                        {"value": "CLASS_21", "count": 13},
                        {"value": "CLASS_22", "count": 12},
                        {"value": "ORG_REG", "count": 11},
                    ]
                },
            }
        ],
        "graphs": [],
    }


def test_entity_type_caps_report_dropped_classes():
    out = infer_baseline_from_snapshot(_capped_snapshot())
    caps = out["entityTypeCaps"]
    assert len(caps) == 1
    cap = caps[0]
    assert cap["collectionName"] == "Node"
    assert cap["typeField"] == "type"
    assert cap["distinctValues"] == 24
    assert cap["exported"] == 20
    assert cap["dropped"] == 4
    dropped = {d["value"] for d in cap["droppedValues"]}
    assert "ORG_REG" in dropped
    assert "droppedValuesTruncated" not in cap
    # The dropped class is genuinely absent from the mapping — the cap record
    # is what makes that non-silent.
    assert "ORGREG" not in out["physicalMapping"]["entities"]
    assert out["relationshipTypeCaps"] == []


def test_caps_flag_truncation_when_overflow_window_is_exceeded():
    snap = _capped_snapshot()
    snap["collections"][0]["sample_field_distinct_counts"]["type"] = 90
    caps, _ = type_value_caps_from_snapshot(snap)
    assert caps[0]["dropped"] == 70
    assert caps[0]["droppedValuesTruncated"] is True


def test_no_caps_when_nothing_dropped():
    out = infer_baseline_from_snapshot(_lpg_snapshot(sample_field_distinct_counts={"type": 2}))
    assert out["entityTypeCaps"] == []
    assert out["relationshipTypeCaps"] == []


def test_edge_collection_drops_feed_relationship_caps():
    snapshot = {
        "version": 2,
        "collections": [
            {
                "name": "edges",
                "type": "edge",
                "count": 500,
                "candidate_type_fields": ["relation"],
                "sample_field_value_counts": {
                    "relation": [{"value": "KNOWS", "count": 300}, {"value": "WORKS_AT", "count": 150}]
                },
                "sample_field_distinct_counts": {"relation": 3},
                "sample_field_value_overflow": {"relation": [{"value": "OWNS", "count": 50}]},
            }
        ],
        "graphs": [],
    }
    entity_caps, rel_caps = type_value_caps_from_snapshot(snapshot)
    assert entity_caps == []
    assert rel_caps[0]["droppedValues"] == [{"value": "OWNS", "count": 50}]


def test_collection_per_entity_mode_reports_no_caps():
    out = infer_baseline_from_snapshot(_capped_snapshot(), collection_per_entity=True)
    assert out["entityTypeCaps"] == []


# ── Issue #2: cap is raisable, and the acceptance bound scales with it ──


def _wide_snapshot_entry(n_values: int):
    return {
        "name": "Node",
        "type": "document",
        "count": n_values * 10,
        "candidate_type_fields": ["type"],
        "sample_field_value_counts": {"type": [{"value": f"CLASS_{i:02d}", "count": 10} for i in range(n_values)]},
    }


def test_raised_top_k_scales_discriminator_acceptance_bound():
    entry = _wide_snapshot_entry(40)
    # Default bound (32) rejects a 40-distinct-value field...
    assert _pick_best_type_field(entry, is_edge=False) is None
    # ...but a caller who asked for up to 50 entity types gets it accepted.
    assert _pick_best_type_field(entry, is_edge=False, max_distinct_values=50) == "type"

    snapshot = {"version": 2, "sample_value_top_k": 50, "collections": [entry], "graphs": []}
    out = infer_baseline_from_snapshot(snapshot)
    assert len(out["physicalMapping"]["entities"]) == 40


def test_default_bound_unchanged_without_raised_top_k():
    snapshot = {"version": 2, "collections": [_wide_snapshot_entry(40)], "graphs": []}
    out = infer_baseline_from_snapshot(snapshot)
    # Field rejected as discriminator → falls back to one entity per collection.
    assert list(out["physicalMapping"]["entities"]) == ["Node"]


# ── Redaction covers the new snapshot keys ──────────────────────────────


def test_redaction_masks_overflow_values_and_new_field_name_keys():
    snapshot = _capped_snapshot()
    opts = RedactionOptions(mask_field_values=True)
    red = redact_snapshot_for_egress(snapshot, opts)
    overflow = red["collections"][0]["sample_field_value_overflow"]["type"]
    assert all("ORG_REG" not in str(item["value"]) for item in overflow)
    assert all(str(item["value"]).startswith("<redacted>") for item in overflow)

    name_map = build_field_name_map(snapshot["collections"])
    assert "type" in name_map
    opts_names = RedactionOptions(mask_field_names=True)
    red_names = redact_snapshot_for_egress(snapshot, opts_names, field_name_map=name_map)
    entry = red_names["collections"][0]
    assert "type" not in entry["sample_field_distinct_counts"]
    assert "type" not in entry["sample_field_value_overflow"]
    assert name_map["type"] in entry["sample_field_value_overflow"]

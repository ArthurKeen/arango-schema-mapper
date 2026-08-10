"""Class-abstraction discovery wiring (PRD §6.3).

The mechanisms live in `conceptual-taxonomy` and are tested there against eight physical
encodings. These tests cover the ASA side: the two inputs that need ArangoDB knowledge,
the merge, and the three integration points (OWL export, grounding validation, extent AQL).
"""

import pytest

from schema_analyzer.errors import SchemaAnalyzerError
from schema_analyzer.mapping import PhysicalMapping
from schema_analyzer.owl_export import _subclass_edges, export_conceptual_model_as_owl_turtle
from schema_analyzer.quality import compute_grounding_metrics
from schema_analyzer.taxonomy import (
    TAXONOMY_AVAILABLE,
    build_discriminators,
    discover,
    merge_into_analysis,
    shard_family_members,
)

requires_taxonomy = pytest.mark.skipif(not TAXONOMY_AVAILABLE, reason="conceptual-taxonomy not installed")

ACCOUNT_PROPS = ["accountId", "name", "description", "balance"]


def sibling_analysis() -> dict:
    """Four account collections sharing a property core — no parent, nothing to inherit."""

    def entity(name, extra):
        return {"name": name, "properties": ACCOUNT_PROPS + extra}

    return {
        "conceptualSchema": {
            "entities": [
                entity("MortgageAccount", ["routingNumber", "principal", "monthlyPayment"]),
                entity("CheckingAccount", ["routingNumber", "overdraftLimit"]),
                entity("SavingsAccount", ["routingNumber", "apy"]),
                entity("InsuranceAccount", ["premium", "policyNumber"]),
                {"name": "Customer", "properties": ["customerId", "email"]},
            ],
            "relationships": [
                {"type": "HAS_CUSTOMER", "fromEntity": n, "toEntity": "Customer"}
                for n in (
                    "MortgageAccount",
                    "CheckingAccount",
                    "SavingsAccount",
                    "InsuranceAccount",
                )
            ],
        },
        "physicalMapping": {
            "entities": {
                n: {"style": "COLLECTION", "collectionName": n}
                for n in (
                    "MortgageAccount",
                    "CheckingAccount",
                    "SavingsAccount",
                    "InsuranceAccount",
                    "Customer",
                )
            }
        },
    }


# ── inputs that need ArangoDB knowledge ──────────────────────────────────────


@requires_taxonomy
def test_discriminators_are_grouped_by_collection_and_field():
    mapping = {
        "entities": {
            "Mortgage": {"style": "LABEL", "collectionName": "accounts", "typeField": "type", "typeValue": "mortgage"},
            "Checking": {"style": "LABEL", "collectionName": "accounts", "typeField": "type", "typeValue": "checking"},
            "Invoice": {"style": "COLLECTION", "collectionName": "invoices"},
        }
    }
    discs = build_discriminators(mapping)
    assert len(discs) == 1
    assert discs[0].container == "accounts"
    assert discs[0].values == ["checking", "mortgage"]
    # Supplied explicitly: the analyzer knows which entity each value produced, so handing
    # over a name-matching guess instead would be strictly worse.
    assert discs[0].entities == {"mortgage": "Mortgage", "checking": "Checking"}
    assert discs[0].parent_entity is None


@requires_taxonomy
def test_whole_collection_entity_becomes_the_specialization_parent():
    mapping = {
        "entities": {
            "Account": {"style": "COLLECTION", "collectionName": "accounts"},
            "Mortgage": {"style": "LABEL", "collectionName": "accounts", "typeField": "type", "typeValue": "mortgage"},
            "Checking": {"style": "LABEL", "collectionName": "accounts", "typeField": "type", "typeValue": "checking"},
        }
    }
    assert build_discriminators(mapping)[0].parent_entity == "Account"


def test_single_value_discriminator_is_not_a_taxonomy():
    mapping = {"entities": {"Only": {"style": "LABEL", "collectionName": "c", "typeField": "t", "typeValue": "x"}}}
    assert build_discriminators(mapping) == []


def test_shard_family_members_are_identified():
    mapping = {
        "shardFamilies": [{"name": "Document", "members": [{"entity": "IbexDocument"}, {"entity": "Or1200Document"}]}]
    }
    assert shard_family_members(mapping) == {"IbexDocument", "Or1200Document"}


@requires_taxonomy
def test_shard_family_members_are_excluded_from_discovery():
    """A family satisfies the concept criteria too and would produce a rival abstraction."""
    analysis = sibling_analysis()
    analysis["physicalMapping"]["shardFamilies"] = [
        {
            "name": "AccountFamily",
            "members": [{"entity": "MortgageAccount"}, {"entity": "CheckingAccount"}],
        }
    ]
    proposals = discover(analysis)
    covered = {c["conceptualClass"] for c in proposals["abstractClasses"]}
    for abstract in proposals["abstractClasses"]:
        assert "MortgageAccount" not in abstract["members"]
        assert "CheckingAccount" not in abstract["members"]
    assert "AccountFamily" not in covered


# ── discovery + merge ────────────────────────────────────────────────────────


@requires_taxonomy
def test_discovers_the_account_hierarchy():
    proposals = discover(sibling_analysis())
    extents = {frozenset(c["members"]) for c in proposals["abstractClasses"]}
    assert frozenset({"MortgageAccount", "CheckingAccount", "SavingsAccount", "InsuranceAccount"}) in extents
    assert frozenset({"MortgageAccount", "CheckingAccount", "SavingsAccount"}) in extents


@requires_taxonomy
def test_aggregate_safety_survives_the_round_trip():
    """`balance` is summable across all accounts; `monthlyPayment` is not."""
    proposals = discover(sibling_analysis())
    top = next(c for c in proposals["abstractClasses"] if len(c["members"]) == 4)

    assert "balance" in {p["name"] for p in top["sharedProperties"]}
    partial = {p["name"]: p for p in top["partialProperties"]}
    assert partial["monthlyPayment"]["presentOn"] == ["MortgageAccount"]


@requires_taxonomy
def test_merge_is_additive_and_marks_classes_abstract():
    analysis = sibling_analysis()
    before = len(analysis["conceptualSchema"]["entities"])
    merged = merge_into_analysis(analysis, discover(sibling_analysis()))

    assert len(merged["conceptualSchema"]["entities"]) > before
    added = [e for e in merged["conceptualSchema"]["entities"] if e.get("abstract")]
    assert added
    assert all(e["name"] not in merged["physicalMapping"]["entities"] for e in added)
    assert merged["conceptualSchema"]["subClassOfProposals"]


def test_merge_with_no_proposals_is_a_no_op():
    analysis = sibling_analysis()
    assert merge_into_analysis(analysis, None) is analysis


def test_discover_degrades_when_dependency_is_missing(monkeypatch):
    """Absent the optional dependency, discovery is skipped rather than fatal."""
    monkeypatch.setattr("schema_analyzer.taxonomy.TAXONOMY_AVAILABLE", False)
    assert discover(sibling_analysis()) is None


# ── integration points ───────────────────────────────────────────────────────


def test_subclass_edges_merges_both_sources():
    data = {
        "physicalMapping": {"shardFamilies": [{"name": "Document", "members": [{"entity": "IbexDocument"}]}]},
        "conceptualSchema": {"subClassOfProposals": [{"subClass": "MortgageAccount", "superClass": "Account"}]},
    }
    edges = _subclass_edges(data)
    assert ("IbexDocument", "Document") in edges
    assert ("MortgageAccount", "Account") in edges


def test_subclass_edges_deduplicates_and_rejects_self_edges():
    data = {
        "conceptualSchema": {
            "subClassOfProposals": [
                {"subClass": "A", "superClass": "B"},
                {"subClass": "A", "superClass": "B"},
                {"subClass": "C", "superClass": "C"},
            ]
        }
    }
    assert _subclass_edges(data) == [("A", "B")]


@requires_taxonomy
def test_owl_export_carries_the_discovered_hierarchy():
    merged = merge_into_analysis(sibling_analysis(), discover(sibling_analysis()))
    ttl = export_conceptual_model_as_owl_turtle(merged)
    assert "rdfs:subClassOf" in ttl
    assert ttl.count("MortgageAccount a owl:Class") == 1  # declared once, not twice


def test_abstract_classes_are_not_flagged_ungrounded():
    """The absent physical mapping is the signal, not a defect."""
    conceptual = {
        "entities": [
            {"name": "Account", "abstract": True},
            {"name": "MortgageAccount"},
            {"name": "Orphan"},
        ]
    }
    physical = {"entities": {"MortgageAccount": {"style": "COLLECTION", "collectionName": "m"}}}
    snapshot = {"collections": [{"name": "m"}]}

    result = compute_grounding_metrics(conceptual, physical, snapshot)
    assert result["unmappedEntities"] == ["Orphan"]


# ── aql_class_extent ─────────────────────────────────────────────────────────


def test_extent_unions_sibling_collections():
    pm = PhysicalMapping()
    out = pm.aql_class_extent(
        variable="a",
        realizations=[
            {"entity": "MortgageAccount", "style": "COLLECTION", "collectionName": "Mortgage"},
            {"entity": "CheckingAccount", "style": "COLLECTION", "collectionName": "Checking"},
        ],
    )
    assert out["query"].startswith("FOR a IN UNION(")
    assert out["bind_vars"]["@extent0"] == "Mortgage"
    assert out["bind_vars"]["@extent1"] == "Checking"


def test_extent_folds_label_members_sharing_a_collection():
    """One pass with an IN, not a UNION of near-identical scans."""
    pm = PhysicalMapping()
    out = pm.aql_class_extent(
        variable="a",
        realizations=[
            {"style": "LABEL", "collectionName": "accounts", "typeField": "type", "typeValue": "mortgage"},
            {"style": "LABEL", "collectionName": "accounts", "typeField": "type", "typeValue": "checking"},
        ],
    )
    assert "UNION" not in out["query"]
    assert out["bind_vars"]["typeValues0"] == ["checking", "mortgage"]


def test_extent_mixes_styles():
    """Sibling-collection and discriminated layouts must be indistinguishable to a consumer."""
    pm = PhysicalMapping()
    out = pm.aql_class_extent(
        variable="a",
        realizations=[
            {"style": "COLLECTION", "collectionName": "Insurance"},
            {"style": "LABEL", "collectionName": "accounts", "typeField": "type", "typeValue": "mortgage"},
        ],
    )
    assert "UNION" in out["query"]
    assert out["bind_vars"]["@extent0"] == "Insurance"
    assert out["bind_vars"]["@extent1"] == "accounts"


def test_whole_collection_subsumes_a_filtered_slice_of_itself():
    pm = PhysicalMapping()
    out = pm.aql_class_extent(
        variable="a",
        realizations=[
            {"style": "COLLECTION", "collectionName": "accounts"},
            {"style": "LABEL", "collectionName": "accounts", "typeField": "type", "typeValue": "mortgage"},
        ],
    )
    assert "UNION" not in out["query"]
    assert "typeField0" not in out["bind_vars"]


def test_extent_never_interpolates_names():
    pm = PhysicalMapping()
    out = pm.aql_class_extent(
        variable="a",
        realizations=[{"style": "LABEL", "collectionName": "accounts", "typeField": "type", "typeValue": "mortgage"}],
    )
    for name in ("accounts", "mortgage"):
        assert name not in out["query"]


@pytest.mark.parametrize(
    "realizations",
    [
        [],
        [{"style": "COLLECTION"}],
        [{"style": "LABEL", "collectionName": "c", "typeField": "t"}],
        [
            {"style": "LABEL", "collectionName": "c", "typeField": "t", "typeValue": "a"},
            {"style": "LABEL", "collectionName": "c", "typeField": "other", "typeValue": "b"},
        ],
    ],
)
def test_extent_rejects_invalid_realizations(realizations):
    with pytest.raises(SchemaAnalyzerError):
        PhysicalMapping().aql_class_extent(variable="a", realizations=realizations)


def test_extent_checks_the_variable():
    with pytest.raises(ValueError):
        PhysicalMapping().aql_class_extent(
            variable="a; RETURN 1",
            realizations=[{"style": "COLLECTION", "collectionName": "c"}],
        )

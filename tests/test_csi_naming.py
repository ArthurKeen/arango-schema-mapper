"""CC-12 OWL naming for CSI conceptual models (contextual-data-fabric CC-12)."""

from schema_analyzer.csi import to_csi, validate_csi
from schema_analyzer.csi.naming import (
    apply_owl_naming,
    lower_camel,
    naming_issues,
    pascal_singular,
)

RAW = {
    "csiVersion": "1",
    "conceptualModel": {
        "entities": [
            {
                "name": "documents",
                "labels": ["documents"],
                "properties": [{"name": "account_id"}, {"name": "citable_url"}, {"name": "_uri"}],
            }
        ],
        "relationships": [{"type": "HAS_CHUNK", "fromEntity": "documents", "toEntity": "chunks"}],
    },
    "arangoPhysicalMapping": {
        "entities": {
            "documents": {
                "style": "COLLECTION",
                "collectionName": "documents",
                "properties": {"account_id": {"field": "account_id", "indexed": True}},
            }
        },
        "relationships": {
            "HAS_CHUNK": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "has_chunk"}
        },
    },
    "provenance": {"producer": "t", "direction": "reverse", "source": {"kind": "arango", "ref": "x"}},
}


def test_pascal_singular_cases():
    assert pascal_singular("usage_metrics") == "UsageMetric"
    assert pascal_singular("nps_surveys") == "NpsSurvey"
    assert pascal_singular("accounts") == "Account"
    assert pascal_singular("Ticket") == "Ticket"
    assert pascal_singular("FIN_METRICS") == "FinMetric"
    assert pascal_singular("people", {"people": "Person"}) == "Person"  # override map


def test_lower_camel_cases():
    assert lower_camel("account_id") == "accountId"
    assert lower_camel("HAS_NAME") == "hasName"
    assert lower_camel("HTTPServer") == "httpServer"
    assert lower_camel("source") == "source"
    assert lower_camel("_uri") == "_uri"  # system fields keep their underscore


def test_apply_owl_naming_renames_conceptual_keeps_physical():
    out = apply_owl_naming(RAW)
    ent = out["conceptualModel"]["entities"][0]
    assert ent["name"] == "Document"
    assert [p["name"] for p in ent["properties"]] == ["accountId", "citableUrl", "_uri"]

    spec = out["arangoPhysicalMapping"]["entities"]["Document"]
    assert spec["collectionName"] == "documents"  # physical untouched
    assert spec["properties"]["accountId"] == {"field": "account_id", "indexed": True}
    assert spec["properties"]["citableUrl"]["field"] == "citable_url"

    rel = out["conceptualModel"]["relationships"][0]
    assert rel["type"] == "hasChunk" and rel["fromEntity"] == "Document"
    assert out["arangoPhysicalMapping"]["relationships"]["hasChunk"]["edgeCollectionName"] == "has_chunk"


def test_apply_owl_naming_is_idempotent():
    once = apply_owl_naming(RAW)
    assert apply_owl_naming(once) == once


def test_naming_issues_flag_raw_and_pass_conforming():
    assert naming_issues(RAW)
    assert naming_issues(apply_owl_naming(RAW)) == []


def test_validate_csi_enforces_naming_by_default():
    conforming = apply_owl_naming(RAW)
    assert validate_csi(conforming) == []
    assert any("CC-12" in e for e in validate_csi(RAW))
    assert validate_csi(RAW, naming=False) == []  # escape hatch


def test_to_csi_applies_owl_naming_by_default():
    analysis = {
        "conceptualSchema": RAW["conceptualModel"],
        "physicalMapping": RAW["arangoPhysicalMapping"],
        "metadata": {"confidence": 0.9},
    }
    doc = to_csi(analysis, source={"kind": "arango", "ref": "x"})
    assert doc["conceptualModel"]["entities"][0]["name"] == "Document"
    assert validate_csi(doc) == []
    raw_doc = to_csi(analysis, source={"kind": "arango", "ref": "x"}, owl_naming=False)
    assert raw_doc["conceptualModel"]["entities"][0]["name"] == "documents"

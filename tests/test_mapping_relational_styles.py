"""FOREIGN_KEY and JOIN_TABLE relationship mappings (PRD §3.3).

Relational-in-ArangoDB patterns: a relationship carried by a scalar attribute, and one
reified as a document collection. Style names are shared with `relational-schema-analyzer`,
which emits both for the same physical patterns.
"""

import pytest

from schema_analyzer.errors import SchemaAnalyzerError
from schema_analyzer.mapping import PhysicalMapping

FK = {
    "style": "FOREIGN_KEY",
    "fromCollection": "Album",
    "fromFields": ["ArtistId"],
    "toCollection": "Artist",
    "toKeyFields": ["_key"],
    "enforced": False,
    "confidence": 0.92,
}

JOIN = {
    "style": "JOIN_TABLE",
    "joinCollection": "PlaylistTrack",
    "fromCollection": "Playlist",
    "joinFromFields": ["PlaylistId"],
    "joinFromParentFields": ["_key"],
    "toCollection": "Track",
    "joinToFields": ["TrackId"],
    "joinToParentFields": ["_key"],
    "attributeFields": [],
    "enforced": False,
}


def _fk_mapping(**overrides):
    return PhysicalMapping(relationships={"HAS_ARTIST": {**FK, **overrides}})


# ── FOREIGN_KEY ──────────────────────────────────────────────────────────────


def test_foreign_key_outbound_resolves_the_referenced_document():
    out = _fk_mapping().aql_relationship_traversal(from_variable="album", rel_type="HAS_ARTIST", to_variable="artist")
    assert "FOR artist IN @@collection" in out["query"]
    assert "artist[@targetField0] == album[@boundField0]" in out["query"]
    assert out["bind_vars"]["@collection"] == "Artist"
    assert out["bind_vars"]["targetField0"] == "_key"
    assert out["bind_vars"]["boundField0"] == "ArtistId"


def test_foreign_key_inbound_finds_the_referencing_documents():
    out = _fk_mapping().aql_relationship_traversal(
        from_variable="artist", rel_type="HAS_ARTIST", to_variable="album", direction="inbound"
    )
    assert out["bind_vars"]["@collection"] == "Album"
    assert out["bind_vars"]["targetField0"] == "ArtistId"
    assert out["bind_vars"]["boundField0"] == "_key"


def test_foreign_key_has_no_edge_document():
    """There is no edge, so a consumer must not assume one exists to hang properties on."""
    out = _fk_mapping().aql_relationship_traversal(from_variable="album", rel_type="HAS_ARTIST", to_variable="artist")
    assert out["edge_variable"] is None


def test_foreign_key_supports_composite_keys():
    out = _fk_mapping(fromFields=["TenantId", "ArtistId"], toKeyFields=["tenant", "_key"]).aql_relationship_traversal(
        from_variable="album", rel_type="HAS_ARTIST", to_variable="artist"
    )
    assert "AND" in out["query"]
    assert out["bind_vars"]["targetField1"] == "_key"
    assert out["bind_vars"]["boundField1"] == "ArtistId"


def test_foreign_key_rejects_mismatched_key_arity():
    with pytest.raises(SchemaAnalyzerError):
        _fk_mapping(fromFields=["A", "B"], toKeyFields=["_key"]).aql_relationship_traversal(
            from_variable="x", rel_type="HAS_ARTIST", to_variable="y"
        )


@pytest.mark.parametrize("missing", ["fromCollection", "toCollection", "fromFields", "toKeyFields"])
def test_foreign_key_rejects_incomplete_mapping(missing):
    payload = {k: v for k, v in FK.items() if k != missing}
    pm = PhysicalMapping(relationships={"HAS_ARTIST": payload})
    with pytest.raises(SchemaAnalyzerError):
        pm.aql_relationship_traversal(from_variable="x", rel_type="HAS_ARTIST", to_variable="y")


# ── JOIN_TABLE ───────────────────────────────────────────────────────────────


def test_join_table_emits_two_hops_for_one_conceptual_hop():
    """The reification is physical; the conceptual relationship stays 1-hop."""
    pm = PhysicalMapping(relationships={"HAS_TRACK": JOIN})
    out = pm.aql_relationship_traversal(from_variable="playlist", rel_type="HAS_TRACK", to_variable="track")
    assert out["query"].count("FOR ") == 2
    assert out["bind_vars"]["@joinCollection"] == "PlaylistTrack"
    assert out["bind_vars"]["@collection"] == "Track"


def test_join_row_is_bound_so_edge_properties_are_reachable():
    pm = PhysicalMapping(relationships={"HAS_TRACK": {**JOIN, "attributeFields": ["addedAt"]}})
    out = pm.aql_relationship_traversal(
        from_variable="playlist", rel_type="HAS_TRACK", to_variable="track", edge_variable="pt"
    )
    assert out["edge_variable"] == "pt"
    assert "FOR pt IN @@joinCollection" in out["query"]


def test_join_table_inbound_swaps_both_ends():
    pm = PhysicalMapping(relationships={"HAS_TRACK": JOIN})
    out = pm.aql_relationship_traversal(
        from_variable="track", rel_type="HAS_TRACK", to_variable="playlist", direction="inbound"
    )
    assert out["bind_vars"]["@collection"] == "Playlist"
    assert out["bind_vars"]["nearJoinField0"] == "TrackId"
    assert out["bind_vars"]["farJoinField0"] == "PlaylistId"


def test_join_table_rejects_incomplete_mapping():
    payload = {k: v for k, v in JOIN.items() if k != "joinCollection"}
    pm = PhysicalMapping(relationships={"HAS_TRACK": payload})
    with pytest.raises(SchemaAnalyzerError):
        pm.aql_relationship_traversal(from_variable="x", rel_type="HAS_TRACK", to_variable="y")


# ── injection safety, matching the existing styles ───────────────────────────


@pytest.mark.parametrize("payload,rel", [(FK, "HAS_ARTIST"), (JOIN, "HAS_TRACK")])
def test_variables_are_still_identifier_checked(payload, rel):
    pm = PhysicalMapping(relationships={rel: payload})
    with pytest.raises(ValueError):
        pm.aql_relationship_traversal(from_variable="a; RETURN 1", rel_type=rel, to_variable="b")


@pytest.mark.parametrize("payload,rel", [(FK, "HAS_ARTIST"), (JOIN, "HAS_TRACK")])
def test_no_collection_or_field_name_is_interpolated(payload, rel):
    """Every schema-derived name travels as a bind variable, never as query text."""
    pm = PhysicalMapping(relationships={rel: payload})
    out = pm.aql_relationship_traversal(from_variable="a", rel_type=rel, to_variable="b")
    query = out["query"]
    for value in payload.values():
        names = value if isinstance(value, list) else [value]
        for name in names:
            if isinstance(name, str) and name not in ("FOREIGN_KEY", "JOIN_TABLE"):
                assert name not in query, f"{name} was interpolated into the query"

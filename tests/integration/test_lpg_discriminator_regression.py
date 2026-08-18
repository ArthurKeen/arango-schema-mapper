"""Real-DB regression for the reserved-word ``COLLECT`` bug (downstream REQ-116).

A single document collection whose class is a discriminator field must be
modelled as an **LPG** — one ``LABEL`` entity per discriminator value — not as a
single ``PG_ENTITY_COLLECTION``.

This test MUST run against a **live ArangoDB**. The bug it guards was an unquoted
AQL reserved word (``distinct``) as a projection key, which ArangoDB rejects with
``[ERR 1501] syntax error`` — a *query-parse* failure. A fake/fixture DB never
parses the AQL, so by construction it cannot catch this class of bug; only a real
server does. The failure was also silently swallowed, so ``sample_field_value_counts``
came back empty and every discriminated collection fell through to PG. See
``schema_analyzer/snapshot.py::_detect_type_fields_via_collect``.

To confirm this test actually guards the bug: revert the quoting of the
``"distinct"`` object key in ``_detect_type_fields_via_collect`` and re-run — it
must fail (empty ``sample_field_value_counts`` / no ``LPG_LABEL``).
"""

from __future__ import annotations

import pytest

from schema_analyzer.baseline import infer_baseline_from_snapshot
from schema_analyzer.snapshot import snapshot_physical_schema

from ..conftest import env

pytestmark = pytest.mark.integration


def test_lpg_discriminator_detected_against_live_db(fresh_database):
    base_db = env("ARANGO_DB", "schema_analyzer_it")
    db = fresh_database(f"{base_db}_lpg_req116")

    db.create_collection("records")
    db.collection("records").import_bulk(
        [
            {"_key": "p1", "kind": "person", "name": "Ada Lovelace"},
            {"_key": "p2", "kind": "person", "name": "Alan Turing"},
            {"_key": "c1", "kind": "company", "name": "Analytical Ltd"},
            {"_key": "v1", "kind": "venue", "name": "Bletchley Park"},
        ]
    )

    snap = snapshot_physical_schema(db)
    col = next(c for c in snap["collections"] if c["name"] == "records")

    # The tell from the bug report: the candidate field IS found, but the value
    # query silently fails, leaving sample_field_value_counts empty.
    assert col["candidate_type_fields"], "expected 'kind' to be a candidate discriminator field"
    assert col["sample_field_value_counts"], (
        "sample_field_value_counts is empty — the discriminator value query failed "
        "(the reserved-word COLLECT bug: ERR 1501, silently swallowed). "
        "This is exactly the regression under guard."
    )

    baseline = infer_baseline_from_snapshot(snap)
    assert "LPG_LABEL" in baseline["detectedPatterns"], (
        f"expected LPG_LABEL, got {baseline['detectedPatterns']} — discriminator detection is inert"
    )

    entities = {e["name"] for e in baseline["conceptualSchema"]["entities"]}
    assert {"Person", "Company", "Venue"} <= entities, (
        f"expected one LABEL entity per discriminator value, got {sorted(entities)}"
    )

    # Each LABEL entity must carry its physical resolution (typeField/typeValue).
    person = baseline["physicalMapping"]["entities"]["Person"]
    assert person["style"] == "LABEL"
    assert person["collectionName"] == "records"
    assert person["typeField"] == "kind"
    assert person["typeValue"] == "person"

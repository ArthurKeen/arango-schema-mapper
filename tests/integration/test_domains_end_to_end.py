import os
import time

import pytest

from arango import ArangoClient

from schema_analyzer import AgenticSchemaAnalyzer
from schema_analyzer.eval import PhysicalVariant, list_domains, load_domain_spec, materialize_domain_variant
from schema_analyzer.snapshot import snapshot_physical_schema


pytestmark = pytest.mark.integration


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v is not None else default


def _skip_if_not_enabled():
    if _env("RUN_INTEGRATION", "0") != "1":
        pytest.skip("integration tests disabled (set RUN_INTEGRATION=1)")


def _connect_root():
    url = _env("ARANGO_URL", "http://localhost:8529")
    user = _env("ARANGO_USER", "root")
    pw = _env("ARANGO_PASS", "openSesame")
    client = ArangoClient(hosts=url)
    return client, client.db("_system", username=user, password=pw)


def _connect_db(db_name: str):
    url = _env("ARANGO_URL", "http://localhost:8529")
    user = _env("ARANGO_USER", "root")
    pw = _env("ARANGO_PASS", "openSesame")
    client = ArangoClient(hosts=url)
    return client.db(db_name, username=user, password=pw)


def _wait_for_arango(sys_db, timeout_s: float = 15.0):
    deadline = time.time() + timeout_s
    last_err = None
    while time.time() < deadline:
        try:
            # cheap call that requires server readiness
            sys_db.has_database("_system")
            return
        except Exception as e:
            last_err = e
            time.sleep(0.5)
    raise RuntimeError(f"ArangoDB not ready after {timeout_s}s: {last_err}")


@pytest.mark.parametrize(
    "variant",
    [
        PhysicalVariant(name="v_collection_dedicated", entity_style="COLLECTION", rel_style="DEDICATED_COLLECTION"),
        PhysicalVariant(name="v_generic_generic", entity_style="GENERIC_WITH_TYPE", rel_style="GENERIC_WITH_TYPE"),
    ],
)
def test_materialize_and_snapshot_domains(variant):
    _skip_if_not_enabled()

    client, sys_db = _connect_root()
    _wait_for_arango(sys_db)
    db_name = _env("ARANGO_DB", "schema_analyzer_it")
    # Use a per-variant DB to avoid cross-test interference.
    db_name = f"{db_name}_{variant.name}"
    if sys_db.has_database(db_name):
        try:
            sys_db.delete_database(db_name)
        except Exception:
            pass
    sys_db.create_database(db_name)
    db = client.db(db_name, username=_env("ARANGO_USER", "root"), password=_env("ARANGO_PASS", "openSesame"))

    domains = list_domains()
    assert domains, "no domain specs found"

    for d in domains:
        spec = load_domain_spec(d)
        materialize_domain_variant(db, spec, variant, seed=1, scale=3, create_graph=True)

    snap = snapshot_physical_schema(db, sample_limit_per_collection=1, include_samples_in_snapshot=False)
    assert snap["collections"], "snapshot should include collections"
    # At least one graph attempt should show up (best-effort).
    assert "graphs" in snap

    # Analyzer runs even without provider (graceful degradation).
    analyzer = AgenticSchemaAnalyzer(llm_provider=None, api_key=None)
    analysis = analyzer.analyze_physical_schema(db, sample_limit_per_collection=1)
    assert analysis.metadata.review_required is True


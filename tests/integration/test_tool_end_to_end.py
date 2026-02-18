import os
import time

import pytest

from arango import ArangoClient

from schema_analyzer.tool import run_tool


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


def _wait_for_arango(sys_db, timeout_s: float = 20.0):
    deadline = time.time() + timeout_s
    last_err = None
    while time.time() < deadline:
        try:
            sys_db.has_database("_system")
            return
        except Exception as e:
            last_err = e
            time.sleep(0.5)
    raise RuntimeError(f"ArangoDB not ready after {timeout_s}s: {last_err}")


def test_tool_snapshot_and_analyze_smoke():
    _skip_if_not_enabled()

    client, sys_db = _connect_root()
    _wait_for_arango(sys_db)

    base_db = _env("ARANGO_DB", "schema_analyzer_it")
    db_name = f"{base_db}_tool_smoke"
    if sys_db.has_database(db_name):
        try:
            sys_db.delete_database(db_name)
        except Exception:
            pass
    sys_db.create_database(db_name)

    db = client.db(db_name, username=_env("ARANGO_USER", "root"), password=_env("ARANGO_PASS", "openSesame"))
    if not db.has_collection("users"):
        db.create_collection("users", edge=False)
    if not db.has_collection("follows"):
        db.create_collection("follows", edge=True)

    users = db.collection("users")
    follows = db.collection("follows")
    a = users.insert({"name": "Alice"}, silent=True)
    b = users.insert({"name": "Bob"}, silent=True)
    follows.insert({"_from": a["_id"], "_to": b["_id"], "relation": "FOLLOWS"}, silent=True)

    req_snapshot = {
        "contractVersion": "1",
        "operation": "snapshot",
        "connection": {
            "url": _env("ARANGO_URL", "http://localhost:8529"),
            "database": db_name,
            "username": _env("ARANGO_USER", "root"),
            "password": _env("ARANGO_PASS", "openSesame"),
        },
        "analysisOptions": {"sampleLimitPerCollection": 1, "includeSamplesInSnapshot": False},
    }
    resp_snapshot = run_tool(req_snapshot)
    assert resp_snapshot["ok"] is True
    snap_cols = [c["name"] for c in resp_snapshot["result"]["snapshot"]["collections"]]
    assert "users" in snap_cols
    assert "follows" in snap_cols

    req_analyze = {
        "contractVersion": "1",
        "operation": "analyze",
        "connection": {
            "url": _env("ARANGO_URL", "http://localhost:8529"),
            "database": db_name,
            "username": _env("ARANGO_USER", "root"),
            "password": _env("ARANGO_PASS", "openSesame"),
        },
        # No LLM config on purpose: baseline inference path should still succeed.
        "analysisOptions": {"sampleLimitPerCollection": 1, "includeSamplesInSnapshot": False, "timeoutMs": 60000},
    }
    resp_analyze = run_tool(req_analyze)
    assert resp_analyze["ok"] is True
    analysis = resp_analyze["result"]["analysis"]
    assert analysis["conceptualSchema"]["entities"], "should infer at least one entity"
    assert analysis["physicalMapping"]["entities"], "should produce entity mappings"


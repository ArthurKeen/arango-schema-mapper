#!/usr/bin/env python3
"""Score FK inference against ground truth (PRD §6.2, plan step B3).

Chinook is a rare gift for this: it is a real relational schema with **declared** foreign
keys, so ``PRAGMA foreign_key_list`` is exact ground truth. Loading its tables into ArangoDB
as plain document collections — FK columns retained, no edge collections — reproduces exactly
the shape the detector exists for, with a known correct answer.

Read-only against the source SQLite; writes only to a scratch ArangoDB database.

    python scripts/eval_fk_inference.py --url http://localhost:8529 --password openSesame
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from schema_analyzer.fk_inference import (  # noqa: E402
    CollectionShape,
    InferenceOptions,
    infer_foreign_keys,
)
from schema_analyzer.fk_sampler import ArangoValueSampler  # noqa: E402

TABLES = [
    "Artist",
    "Genre",
    "MediaType",
    "Album",
    "Employee",
    "Customer",
    "Track",
    "Playlist",
    "Invoice",
    "PlaylistTrack",
    "InvoiceLine",
]


def ground_truth(conn: sqlite3.Connection) -> set[tuple[str, str, str]]:
    """``(table, column, foreign_table)`` for every declared FK."""
    truth: set[tuple[str, str, str]] = set()
    for table in TABLES:
        for row in conn.execute(f"PRAGMA foreign_key_list({table})"):
            truth.add((table, row[3], row[2]))
    return truth


def primary_key(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})") if r[5]]


def load_into_arango(conn: sqlite3.Connection, db: Any) -> None:
    """Documents only — no edge collections. The detector must find the relationships."""
    for table in TABLES:
        if db.has_collection(table):
            db.delete_collection(table)
        db.create_collection(table)
        pk = primary_key(conn, table)
        docs = []
        for row in conn.execute(f"SELECT * FROM {table}"):
            doc = dict(row)
            doc["_key"] = "__".join(str(doc[c]) for c in pk)
            docs.append(doc)
        if docs:
            db.collection(table).import_bulk(docs, on_duplicate="replace")


def _category(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    return "string"


def shapes_from_live_db(db: Any, sample: int = 50) -> dict[str, CollectionShape]:
    """Build detector input the way a snapshot would."""
    shapes: dict[str, CollectionShape] = {}
    for table in TABLES:
        rows = list(db.aql.execute(f"FOR d IN `{table}` LIMIT @n RETURN d", bind_vars={"n": sample}))
        fields: dict[str, str] = {}
        values: dict[str, list[Any]] = defaultdict(list)
        for row in rows:
            for key, value in row.items():
                if value is None:
                    continue
                fields.setdefault(key, _category(value))
                if len(values[key]) < sample:
                    values[key].append(value)
        shapes[table] = CollectionShape(
            name=table,
            fields=fields,
            key_fields=["_key"],
            sample_values=dict(values),
            count=db.collection(table).count(),
        )
    return shapes


def score(found: set[tuple[str, str, str]], truth: set[tuple[str, str, str]]) -> dict[str, Any]:
    tp = len(found & truth)
    precision = tp / len(found) if found else 0.0
    recall = tp / len(truth) if truth else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "tp": tp,
        "fp": sorted(found - truth),
        "fn": sorted(truth - found),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8529")
    parser.add_argument("--database", default="fk_eval")
    parser.add_argument("--username", default="root")
    parser.add_argument("--password", default="openSesame")
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=Path.home() / "code/auto-tool-research/data/Chinook_Sqlite.sqlite",
    )
    args = parser.parse_args()

    if "localhost" not in args.url and "127.0.0.1" not in args.url:
        print("refusing to run against a non-local URL", file=sys.stderr)
        return 2

    from arango import ArangoClient

    conn = sqlite3.connect(args.sqlite)
    conn.row_factory = sqlite3.Row
    truth = ground_truth(conn)

    client = ArangoClient(hosts=args.url)
    sys_db = client.db("_system", username=args.username, password=args.password)
    if not sys_db.has_database(args.database):
        sys_db.create_database(args.database)
    db = client.db(args.database, username=args.username, password=args.password)

    print(f"loading Chinook into {args.url}/{args.database} (documents only, no edges)")
    load_into_arango(conn, db)
    shapes = shapes_from_live_db(db)
    conn.close()

    print(f"\nground truth: {len(truth)} declared foreign keys")
    for item in sorted(truth):
        print(f"  {item[0]}.{item[1]} -> {item[2]}")

    print("\n── threshold sweep (names only, no containment probing) ──")
    print(f"{'min_conf':>9} {'P':>6} {'R':>6} {'F1':>6}   fp  fn")
    for threshold in (0.4, 0.5, 0.55, 0.6, 0.7, 0.8, 0.85):
        results = infer_foreign_keys(shapes, options=InferenceOptions(min_confidence=threshold))
        found = {(r.collection, r.fields[0], r.foreign_collection) for r in results if r.fields}
        s = score(found, truth)
        print(
            f"{threshold:>9} {s['precision']:>6} {s['recall']:>6} {s['f1']:>6}   {len(s['fp']):>2}  {len(s['fn']):>2}"
        )

    print("\n── with containment probing ──")
    sampler = ArangoValueSampler(db)
    opts = InferenceOptions(sample_overlap=True)
    results = infer_foreign_keys(shapes, options=opts, sampler=sampler)
    found = {(r.collection, r.fields[0], r.foreign_collection) for r in results if r.fields}
    s = score(found, truth)
    print(f"precision={s['precision']} recall={s['recall']} f1={s['f1']} probes={sampler.probes}")
    print(f"sampler status: {sampler.status()['status']}")
    if s["fp"]:
        print("\nfalse positives:")
        for item in s["fp"]:
            print(f"  {item[0]}.{item[1]} -> {item[2]}")
    if s["fn"]:
        print("\nmissed (false negatives):")
        for item in s["fn"]:
            print(f"  {item[0]}.{item[1]} -> {item[2]}")

    print("\ndetected relationships:")
    for r in sorted(results, key=lambda r: -r.confidence):
        mark = "OK " if (r.collection, r.fields[0], r.foreign_collection) in truth else "FP "
        print(f"  {mark} {r.confidence:>5}  {r.collection}.{r.fields[0]} -> {r.foreign_collection}  [{r.method}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

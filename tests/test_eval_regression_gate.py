"""Tests for the eval regression gate (PRD §3.12.3 — CI compares reports to baselines)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from schema_analyzer.eval import report_regressions


def _run_entry(domain: str, variant: str, *, ent_f1=0.8, rel_f1=0.7, dr_f1=0.6, map_acc=1.0, conf=0.1):
    return {
        "domain": domain,
        "variant": variant,
        "confidence": conf,
        "score": {"entities": {"f1": ent_f1}, "relationships": {"f1": rel_f1}},
        "domain_range": {"f1": dr_f1},
        "mapping_style": {"relationships": {"accuracy": map_acc}},
    }


def _write(tmp_path: Path, name: str, runs: list[dict]) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps({"runs": runs}), "utf-8")
    return p


def test_identical_reports_have_no_regressions(tmp_path: Path):
    runs = [_run_entry("healthcare", "collection_dedicated")]
    cur = _write(tmp_path, "cur.json", runs)
    base = _write(tmp_path, "base.json", runs)
    assert report_regressions(cur, base) == []


def test_metric_drop_beyond_threshold_is_flagged(tmp_path: Path):
    base_runs = [_run_entry("healthcare", "collection_dedicated", rel_f1=0.7)]
    cur_runs = [_run_entry("healthcare", "collection_dedicated", rel_f1=0.6)]
    cur = _write(tmp_path, "cur.json", cur_runs)
    base = _write(tmp_path, "base.json", base_runs)
    regs = report_regressions(cur, base)
    assert len(regs) == 1
    assert "rel_f1" in regs[0] and "healthcare|collection_dedicated" in regs[0]


def test_improvement_and_tiny_jitter_pass(tmp_path: Path):
    base_runs = [_run_entry("healthcare", "collection_dedicated", ent_f1=0.800)]
    cur_runs = [_run_entry("healthcare", "collection_dedicated", ent_f1=0.799)]  # within threshold
    cur = _write(tmp_path, "cur.json", cur_runs)
    base = _write(tmp_path, "base.json", base_runs)
    assert report_regressions(cur, base) == []

    cur_runs = [_run_entry("healthcare", "collection_dedicated", ent_f1=0.95)]  # improvement
    cur = _write(tmp_path, "cur2.json", cur_runs)
    assert report_regressions(cur, base) == []


def test_confidence_drop_is_not_gated(tmp_path: Path):
    base_runs = [_run_entry("healthcare", "collection_dedicated", conf=0.9)]
    cur_runs = [_run_entry("healthcare", "collection_dedicated", conf=0.1)]
    cur = _write(tmp_path, "cur.json", cur_runs)
    base = _write(tmp_path, "base.json", base_runs)
    assert report_regressions(cur, base) == []


def test_baseline_entry_missing_from_current_is_flagged(tmp_path: Path):
    base_runs = [
        _run_entry("healthcare", "collection_dedicated"),
        _run_entry("insurance", "collection_dedicated"),
    ]
    cur_runs = [base_runs[0]]
    cur = _write(tmp_path, "cur.json", copy.deepcopy(cur_runs))
    base = _write(tmp_path, "base.json", base_runs)
    regs = report_regressions(cur, base)
    assert len(regs) == 1
    assert "insurance|collection_dedicated" in regs[0] and "missing" in regs[0]


def test_legacy_bare_list_reports_accepted(tmp_path: Path):
    runs = [_run_entry("healthcare", "collection_dedicated")]
    cur = tmp_path / "cur.json"
    cur.write_text(json.dumps(runs), "utf-8")  # legacy shape: bare list
    base = _write(tmp_path, "base.json", runs)
    assert report_regressions(cur, base) == []

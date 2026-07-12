"""Offline tests for verify_run.py -- the mechanized integrity checklist. No network,
no scipy: verify-run is stdlib-only. Covers the six checks plus the CLI exit code."""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def mkrun(seed, results, smoke=False, notebook="exp.ipynb", span=None):
    return {"notebook": notebook, "seed": seed, "smoke_test": smoke,
            "span_start": span, "timestamp_utc": "2026-07-12T00:00:00+00:00",
            "results": results}


def ok(mape=3.5, params=1000):
    return {"rmse": 600.0, "mae": 450.0, "mape": mape, "params": params,
            "futility_stop": None}


def test_is_bad_number(verify):
    assert verify.is_bad_number(None) is True
    assert verify.is_bad_number(float("nan")) is True
    assert verify.is_bad_number(float("inf")) is True
    assert verify.is_bad_number(3.5) is False
    assert verify.is_bad_number(0) is False


def test_parse_anchors(verify):
    assert verify.parse_anchors("1A=45393,MTGNN=76705") == {"1A": 45393, "MTGNN": 76705}
    assert verify.parse_anchors("") == {}
    assert verify.parse_anchors(None) == {}
    with pytest.raises(SystemExit):
        verify.parse_anchors("1A=notanint")


def test_check_run_clean(verify):
    run = mkrun(0, {"A": ok(params=100), "B": ok(params=200)})
    f = verify.check_run("p.json", run, expect=["A", "B"], anchors={"A": 100})
    assert f["fail"] is False
    assert f["missing"] == [] and f["nan"] == [] and f["anchor_mismatch"] == []


def test_check_run_missing_config_is_failure(verify):
    run = mkrun(0, {"A": ok()})
    f = verify.check_run("p.json", run, expect=["A", "B", "MTGNN"])
    assert f["fail"] is True
    assert f["missing"] == ["B", "MTGNN"]


def test_check_run_nan_metric_is_failure(verify):
    run = mkrun(0, {"A": {"rmse": 1.0, "mae": 1.0, "mape": float("nan"), "params": 10,
                          "futility_stop": None},
                    "B": {"rmse": None, "mae": 1.0, "mape": 1.0, "params": 10,
                          "futility_stop": None}})
    f = verify.check_run("p.json", run)
    assert f["fail"] is True
    bad = dict(f["nan"])
    assert "mape" in bad["A"] and "rmse" in bad["B"]


def test_check_run_futility_is_disclosed_not_failed(verify):
    # a futility stop with otherwise-finite metrics is a disclosure, not a hard failure
    run = mkrun(0, {"A": {"rmse": 1.0, "mae": 1.0, "mape": 9.9, "params": 10,
                          "futility_stop": "no_progress_vs_trivial_baseline"}})
    f = verify.check_run("p.json", run)
    assert f["fail"] is False
    assert f["warn"] is True
    assert f["futility"] == [("A", "no_progress_vs_trivial_baseline")]


def test_check_run_anchor_mismatch_is_failure(verify):
    run = mkrun(0, {"A": ok(params=45393)})
    f = verify.check_run("p.json", run, anchors={"A": 999})
    assert f["fail"] is True
    assert f["anchor_mismatch"] == [("A", 999, 45393)]


def test_smoke_flag_disclosed(verify):
    run = mkrun(0, {"A": ok()}, smoke=True)
    f = verify.check_run("p.json", run)
    assert f["smoke"] is True and f["warn"] is True and f["fail"] is False


def test_seed_summary_single_seed_flag_and_smoke_excluded(verify):
    findings = [
        verify.check_run("a.json", mkrun(0, {"A": ok()})),
        verify.check_run("b.json", mkrun(0, {"A": ok()}, smoke=True)),  # excluded
    ]
    studies = verify.seed_summary(findings)
    (key, st), = studies.items()
    assert sorted(s for s in st["seeds"] if s is not None) == [0]  # smoke run not counted


def test_seed_summary_multi_seed(verify):
    findings = [verify.check_run(f"{s}.json", mkrun(s, {"A": ok()})) for s in range(10)]
    studies = verify.seed_summary(findings)
    (key, st), = studies.items()
    assert len(st["seeds"]) == 10


def test_cli_exit_codes(tmp_path):
    """End-to-end: clean glob exits 0; a wrong anchor exits 1."""
    d = tmp_path / "runs" / "seed0" / "output"
    d.mkdir(parents=True)
    (d / "results.json").write_text(json.dumps(mkrun(0, {"A": ok(params=100)})), encoding="utf-8")
    glob = str(tmp_path / "runs" / "*" / "output" / "results.json")
    env = dict(os.environ, PYTHONUTF8="1")
    script = os.path.join(ROOT, "skills", "verify-run", "verify_run.py")

    clean = subprocess.run([sys.executable, script, "--runs-glob", glob],
                           capture_output=True, encoding="utf-8", env=env)
    assert clean.returncode == 0, clean.stdout + clean.stderr

    bad = subprocess.run([sys.executable, script, "--runs-glob", glob,
                          "--anchors", "A=999999"],
                         capture_output=True, encoding="utf-8", env=env)
    assert bad.returncode == 1
    assert "PARAM ANCHOR mismatch" in bad.stdout

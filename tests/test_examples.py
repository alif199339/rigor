"""End-to-end tests for the bundled examples/ demos, run through the CLI exactly as
documented in examples/README.md. They pin the walkthroughs to reality: if a script's
CLI or output wording changes, these fail and the docs must be updated with them."""
import glob
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_GLOB = os.path.join("examples", "stat-check-demo", "runs", "*", "output", "results.json")
STUDIES = os.path.join("examples", "stat-check-demo", "studies.json")


def run_cli(args):
    env = dict(os.environ, PYTHONUTF8="1")
    p = subprocess.run([sys.executable] + args, cwd=ROOT, capture_output=True,
                       encoding="utf-8", env=env)
    assert p.returncode == 0, p.stderr
    return p.stdout


def test_demo_runs_are_valid_results_files():
    files = sorted(glob.glob(os.path.join(ROOT, RUNS_GLOB)))
    assert len(files) == 8
    seeds = set()
    for f in files:
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        assert d["smoke_test"] is False
        assert set(d["results"]) == {"small_A", "small_B", "big_baseline"}
        seeds.add(d["seed"])
    assert seeds == set(range(8))


def test_stat_check_demo_verdicts():
    pytest.importorskip("scipy")
    out = run_cli([os.path.join("skills", "stat-check", "stat_check.py"),
                   "--runs-glob", RUNS_GLOB, "--studies", STUDIES,
                   "--study", "demo",
                   "--pairs", "small_A:small_B,small_A:big_baseline", "--holm"])
    # the noise pair must NOT be significant; the real gap must survive Holm
    assert "no sig. diff" in out
    assert "**small_A wins** (Holm-adj)" in out
    assert "0.0078" in out  # two-sided Wilcoxon floor at n=8, disclosed exactly


def test_claims_audit_demo_buckets(tmp_path):
    out_md = tmp_path / "report.md"
    out = run_cli([os.path.join("skills", "claims-audit", "claims_audit.py"),
                   "--tex", os.path.join("examples", "claims-audit-demo", "paper.tex"),
                   "--tables", os.path.join("examples", "claims-audit-demo", "tables"),
                   "--results", RUNS_GLOB, "--studies", STUDIES, "--out", str(out_md)])
    assert "4 claims: 2 matched, 1 near-miss, 1 orphan" in out
    report = out_md.read_text(encoding="utf-8")
    assert "`11.21`" in report  # the seeded stale-drift number lands in NEAR-MISS
    assert "`47.5`" in report   # the seeded orphan

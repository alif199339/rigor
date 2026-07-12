"""Offline tests for templates/runner.py pure helpers: run-id namespacing, the journal
append/dedup logic (a fresh-ID retry must never be masked by a stale complete row), and
results.json summary/casting. papermill is imported lazily inside prepare_run(), so
importing the module here needs only pyyaml -- no Kaggle, no network."""


def test_run_id_for_namespaces_by_prefix(runner):
    sweep = {"kernel_slug_prefix": "myproj-exp1"}
    assert runner.run_id_for(sweep, 1) == "myproj-exp1-run001"
    assert runner.run_id_for(sweep, 42) == "myproj-exp1-run042"


def test_run_row_regex_matches_prefixed_and_bare(runner):
    assert runner.RUN_ROW_RE.match("| myproj-exp1-run003 | acct1 | ...").group(1) == "myproj-exp1-run003"
    assert runner.RUN_ROW_RE.match("| run001 | acct1 | ...").group(1) == "run001"
    assert runner.RUN_ROW_RE.match("| header | x |") is None


def _point_journal_at(runner, tmp_path, monkeypatch):
    md = tmp_path / "experiments.md"
    monkeypatch.setattr(runner, "EXPERIMENTS_MD", md)
    monkeypatch.setattr(runner, "EXPERIMENTS_LOCK", tmp_path / "experiments.md.lock")
    return md


def test_append_and_read_back(runner, tmp_path, monkeypatch):
    _point_journal_at(runner, tmp_path, monkeypatch)
    runner.append_experiment_row(dict(
        run_id="myproj-run001", account="acct1", params={"SEED": 0},
        status="complete", mape_summary="A=3.50%", runtime_s=512.0,
        timestamp="2026-07-12T00:00:00+00:00"))
    assert runner.completed_run_ids() == {"myproj-run001"}


def test_completed_ids_only_counts_complete_rows(runner, tmp_path, monkeypatch):
    _point_journal_at(runner, tmp_path, monkeypatch)
    for rid, status in [("p-run001", "complete"), ("p-run002", "FAILED (timeout)"),
                        ("p-run003", "dry_run"), ("p-run004", "complete")]:
        runner.append_experiment_row(dict(
            run_id=rid, account="a", params={}, status=status,
            mape_summary="-", runtime_s=1.0, timestamp="t"))
    assert runner.completed_run_ids() == {"p-run001", "p-run004"}


def test_fresh_id_retry_is_not_masked(runner, tmp_path, monkeypatch):
    """The documented safety property: a failed run retried under a FRESH run-id is not
    treated as already-done, so --resume will actually re-run it. (A reused id would be
    masked -- which is exactly why retries must use a fresh kernel_slug_prefix.)"""
    _point_journal_at(runner, tmp_path, monkeypatch)
    runner.append_experiment_row(dict(
        run_id="p-run001", account="a", params={}, status="complete",
        mape_summary="-", runtime_s=1.0, timestamp="t"))
    done = runner.completed_run_ids()
    assert "p-run001" in done          # the completed one is skipped by --resume
    assert "p-retry-run001" not in done  # a fresh-id retry is NOT masked -> it re-runs


def test_summarize_mape_casts_and_skips(runner):
    parsed = {"results": {
        "A": {"mape": 3.5},          # float
        "B": {"mape": None},         # skipped (no usable number)
        "C": {"mape": "4.2"},        # str (numpy scalar under json default=str) -> cast
        "D": {"mape": "not-a-num"},  # uncastable -> skipped, never crashes the summary
    }}
    out = runner.summarize_mape(parsed)
    assert "A=3.50%" in out and "C=4.20%" in out
    assert "B=" not in out and "D=" not in out


def test_summarize_mape_empty(runner):
    assert runner.summarize_mape(None) == "-"
    assert runner.summarize_mape({}) == "-"
    assert runner.summarize_mape({"results": {"A": {"mape": None}}}) == "-"

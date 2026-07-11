"""Offline tests for stat_check.py: grouping, supersede, pairing, exact p-values."""
import json


def make_runs(tmp_path, n_seeds=5, offset=0.5):
    """A study where config B is worse than A by a constant offset on every seed."""
    runs = tmp_path / "runs"
    for s in range(n_seeds):
        d = runs / f"r{s}" / "output"
        d.mkdir(parents=True)
        a = 3.0 + 0.01 * s
        (d / "results.json").write_text(json.dumps({
            "notebook": "exp.ipynb", "span_start": None, "seed": s,
            "smoke_test": False, "timestamp_utc": f"2026-01-0{s + 1}T00:00:00",
            "results": {"A": {"mape": a, "rmse": 10 * a, "mae": 8 * a, "params": 100},
                        "B": {"mape": a + offset, "rmse": 10 * (a + offset),
                              "mae": 8 * (a + offset), "params": 900}}}))
    return str(runs / "*" / "output" / "results.json")


def test_load_studies_auto_names_and_aligns_seeds(stat, tmp_path):
    g = make_runs(tmp_path)
    studies = stat.load_studies(g, study_map=None)
    assert list(studies) == ["exp"]                       # auto-named from notebook stem
    assert set(studies["exp"]) == {"A", "B"}
    assert sorted(studies["exp"]["A"]) == [0, 1, 2, 3, 4]  # keyed by seed


def test_supersede_newest_run_per_seed(stat, tmp_path):
    g = make_runs(tmp_path, n_seeds=2)
    # add a NEWER re-run of seed 0 with a different value
    d = tmp_path / "runs" / "retry" / "output"
    d.mkdir(parents=True)
    (d / "results.json").write_text(json.dumps({
        "notebook": "exp.ipynb", "span_start": None, "seed": 0,
        "smoke_test": False, "timestamp_utc": "2026-02-01T00:00:00",
        "results": {"A": {"mape": 9.99, "rmse": 1, "mae": 1, "params": 100}}}))
    studies = stat.load_studies(g, study_map=None)
    assert studies["exp"]["A"][0]["mape"] == 9.99          # newest wins


def test_nan_and_none_mape_dropped(stat, tmp_path):
    d = tmp_path / "runs" / "r0" / "output"
    d.mkdir(parents=True)
    (d / "results.json").write_text(json.dumps({
        "notebook": "exp.ipynb", "span_start": None, "seed": 0,
        "smoke_test": False, "timestamp_utc": "t",
        "results": {"A": {"mape": None, "rmse": 1, "mae": 1},
                    "B": {"mape": float("nan"), "rmse": 1, "mae": 1}}}))
    studies = stat.load_studies(str(tmp_path / "runs" / "*" / "output" / "results.json"))
    assert studies.get("exp", {}) == {}                    # both dropped


def test_pair_exact_wilcoxon_floor(stat, tmp_path):
    # all 5 paired diffs same sign -> exact two-sided Wilcoxon p = 2/2^5 = 0.0625
    g = make_runs(tmp_path, n_seeds=5, offset=0.5)
    data = stat.load_studies(g)["exp"]
    r = stat.test_pair(data, "A", "B", "mape")
    assert r["n"] == 5
    assert abs(r["w_p"] - 0.0625) < 1e-9
    assert abs(r["mean_diff"] + 0.5) < 1e-9                # A better -> negative diff
    assert r["median_diff"] < 0


def test_pair_identical_models_p_one(stat, tmp_path):
    g = make_runs(tmp_path, n_seeds=4, offset=0.0)
    data = stat.load_studies(g)["exp"]
    r = stat.test_pair(data, "A", "B", "mape")
    assert r["w_p"] == 1.0 and r["note"] == "identical on every seed"


def test_pair_too_few_seeds(stat, tmp_path):
    g = make_runs(tmp_path, n_seeds=2)
    data = stat.load_studies(g)["exp"]
    r = stat.test_pair(data, "A", "B", "mape")
    assert r["w_p"] is None and "too few" in r["note"]


def test_default_battery_best_vs_runnerup_and_baselines(stat, tmp_path):
    g = make_runs(tmp_path)
    data = stat.load_studies(g)["exp"]
    pairs = stat.default_battery(data, "mape", heavy_names=["B"])
    assert pairs == [("A", "B")]                            # deduped: same pair twice


def test_holm_thresholds(stat):
    results = [{"w_p": 0.001}, {"w_p": 0.04}, {"w_p": 0.5}]
    d = stat.holm(results, alpha=0.05)
    assert d[0][1] is True                                  # 0.001 <= 0.05/3
    assert d[2][1] is False                                 # 0.5 rejected

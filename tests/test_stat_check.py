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


def make_jittered_runs(tmp_path, n_seeds=8, offset=0.0, jitter=0.01):
    """B = A + offset +/- jitter (alternating) -> paired diffs with real variance."""
    runs = tmp_path / "runs"
    for s in range(n_seeds):
        d = runs / f"r{s}" / "output"
        d.mkdir(parents=True)
        a = 3.0 + 0.01 * s
        b = a + offset + (jitter if s % 2 == 0 else -jitter)
        (d / "results.json").write_text(json.dumps({
            "notebook": "exp.ipynb", "span_start": None, "seed": s,
            "smoke_test": False, "timestamp_utc": f"2026-01-{s + 1:02d}T00:00:00",
            "results": {"A": {"mape": a}, "B": {"mape": b}}}))
    return str(runs / "*" / "output" / "results.json")


def test_tost_declares_equivalence_when_diff_tiny(stat, tmp_path):
    g = make_jittered_runs(tmp_path, offset=0.0, jitter=0.01)
    data = stat.load_studies(g)["exp"]
    r = stat.test_pair(data, "A", "B", "mape", tost_margin=0.2)
    assert r["tost_p"] is not None and r["tost_p"] < 0.05    # equivalent at ±0.2
    lo, hi = r["ci95"]
    assert -0.2 < lo <= hi < 0.2                             # CI inside the margin


def test_tost_inconclusive_when_diff_exceeds_margin(stat, tmp_path):
    g = make_jittered_runs(tmp_path, offset=0.5, jitter=0.01)
    data = stat.load_studies(g)["exp"]
    r = stat.test_pair(data, "A", "B", "mape", tost_margin=0.1)
    assert r["tost_p"] > 0.5                                 # cannot claim equivalence


def test_tost_zero_variance_paths(stat, tmp_path):
    # constant offset -> sd == 0 -> the degenerate TOST branch
    g = make_runs(tmp_path, n_seeds=4, offset=0.5)
    data = stat.load_studies(g)["exp"]
    assert stat.test_pair(data, "A", "B", "mape", tost_margin=0.1)["tost_p"] == 1.0
    g2 = make_runs(tmp_path / "z", n_seeds=4, offset=0.0)
    data2 = stat.load_studies(g2)["exp"]
    r = stat.test_pair(data2, "A", "B", "mape", tost_margin=0.1)
    assert r["tost_p"] == 0.0 and r["note"] == "identical on every seed"


def test_ci95_and_effect_size(stat, tmp_path):
    g = make_jittered_runs(tmp_path, offset=0.5, jitter=0.01)
    data = stat.load_studies(g)["exp"]
    r = stat.test_pair(data, "A", "B", "mape")
    lo, hi = r["ci95"]
    assert lo < r["mean_diff"] < hi and lo < -0.45 and hi > -0.55  # brackets -0.5
    assert r["dz"] < -5                                       # huge standardized effect


def test_holm_thresholds(stat):
    results = [{"w_p": 0.001}, {"w_p": 0.04}, {"w_p": 0.5}]
    d = stat.holm(results, alpha=0.05)
    assert d[0][1] is True                                  # 0.001 <= 0.05/3
    assert d[2][1] is False                                 # 0.5 rejected

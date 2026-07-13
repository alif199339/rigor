"""Offline tests for claims_audit.py: scrubbing, extraction, classification, study naming."""


def test_scrub_removes_nonclaim_numbers(claims):
    body = (r"As shown in \cite{yu2018} and Table~\ref{tab:x99}, the model reaches "
            r"3.46\% MAPE. \input{tables/results2024.tex} "
            r"\includegraphics[width=0.46\textwidth]{fig1.pdf} margin 3pt wide, "
            r"\pend[9.99] pending.")
    toks = [c["tok"] for c in claims.extract_claims(claims.scrub_for_claims(body))]
    assert toks == ["3.46"]  # cite key, ref, input path, layout dims, \pend all gone


def test_extract_claims_handles_thousands_separators(claims):
    body = "the compact model has 45,393 parameters vs 121,729"
    vals = [c["val"] for c in claims.extract_claims(body)]
    assert vals == [45393.0, 121729.0]


def test_extract_pending_bare_and_filled(claims):
    body = r"value \pend[3.4] here and a bare \pend hole"
    assert claims.extract_pending(body) == ["3.4", "??.??"]


def test_classify_small_metric_tolerances(claims):
    pool = [(3.46, "tab")]
    assert claims.classify(3.46, pool)[0] == "MATCHED"
    assert claims.classify(3.465, pool)[0] == "MATCHED"      # within rounding
    assert claims.classify(3.53, pool)[0] == "NEAR-MISS"     # drift
    assert claims.classify(9.99, pool)[0] == "ORPHAN"


def test_classify_large_count_relative_tolerance(claims):
    pool = [(45393.0, "res")]
    assert claims.classify(45393, pool)[0] == "MATCHED"
    assert claims.classify(45000, pool)[0] == "NEAR-MISS"    # ~0.9% off -> drift suspect
    assert claims.classify(76000, pool)[0] == "ORPHAN"


def test_study_for_map_vs_auto(claims):
    m = {"exp.ipynb::": "main", "exp.ipynb::2021-01-01": "short"}
    assert claims.study_for("exp.ipynb", None, m) == "main"
    assert claims.study_for("exp.ipynb", "2021-01-01", m) == "short"
    assert claims.study_for("probe.ipynb", None, m) is None          # curated: skipped
    assert claims.study_for("exp.ipynb", None, None) == "exp"        # auto mode
    assert claims.study_for("exp.ipynb", "2021-01-01", None) == "exp@2021-01-01"


def test_pool_from_results_supersede_and_smoke_skip(claims, tmp_path):
    import json
    runs = tmp_path / "runs"
    def put(rid, seed, ts, mape, smoke=False):
        d = runs / rid / "output"
        d.mkdir(parents=True)
        (d / "results.json").write_text(json.dumps({
            "notebook": "exp.ipynb", "span_start": None, "seed": seed,
            "smoke_test": smoke, "timestamp_utc": ts,
            "results": {"A": {"mape": mape, "rmse": 1.0, "mae": 1.0, "params": 100}}}))
    put("r1", 0, "2026-01-01T00:00:00", 3.00)
    put("r2", 0, "2026-01-02T00:00:00", 4.00)          # newer run, same seed: supersedes
    put("r3", 1, "2026-01-01T00:00:00", 6.00, smoke=True)  # smoke: excluded
    pool = claims.pool_from_results(str(runs / "*" / "output" / "results.json"))
    vals = {v for v, _src in pool}
    assert 4.0 in vals and 3.0 not in vals and 6.0 not in vals


def test_markdown_manuscript_support(claims, tmp_path):
    # NB: the claim extractor keys on DECIMAL numbers (integers are structural by design)
    md = """---
title: 'X: a toolkit'
version: 9.9
---

# Summary

The audit MAPE fell from 3.56 to 3.48 [@smith2020, 4.44].

```python
x = 9.999  # code, not a claim
```

See [the docs](https://example.org/v9.99) and `inline_7.77_code`.
"""
    p = tmp_path / "paper.md"
    p.write_text(md, encoding="utf-8")
    body = claims.load_body(str(p))
    assert "title:" not in body                          # frontmatter stripped
    toks = [c["tok"] for c in
            claims.extract_claims(claims.scrub_for_claims(body, markdown=True))]
    assert "3.56" in toks and "3.48" in toks             # prose claims survive
    for gone in ("9.9", "4.44", "9.999", "7.77"):
        assert gone not in toks                          # metadata/cites/code/URLs scrubbed


def test_markdown_images_and_tex_untouched(claims):
    body = r"prose ![alt text](figures/plot_v2.png) and \incfig{regime.pdf}"
    figs = claims.extract_figures(body)
    assert "figures/plot_v2.png" in figs and "regime.pdf" in figs

---
name: stat-check
description: Paired-by-seed significance testing for "model X beats model Y" claims from multi-seed Kaggle runs. Reads results.json across seeds, runs Wilcoxon signed-rank + paired t on requested model pairs, reports exact p-values, n, and median paired difference (optional Holm correction). Use before writing any "beats/outperforms/ties" claim in the manuscript, or to check whether a nominal MAPE gap clears seed noise.
---

# /stat-check — is that "X beats Y" gap real, or seed noise?

The manuscript makes many "compact beats heavy" / "config A beats config B" claims. At
n≈10 seeds, a 0.03 pp MAPE gap is noise and a 0.7 pp gap is decisive — but the mean±std
in a table doesn't tell you *which*. This skill runs the proper **paired-by-seed** test
so each claim is backed by an exact p-value.

The script is `.claude/skills/stat-check/stat_check.py`. It needs **scipy** (any Python
env that has it; the profile's `python_scipy`):

```powershell
$P = "<python-with-scipy>"                              # the profile's python_scipy
$S = ".claude\skills\stat-check\stat_check.py"
$G = "<sweep_dir>\runs\*\output\results.json"           # the profile's results_glob
& $P $S --runs-glob $G --list                            # studies + configs + seeds
& $P $S --runs-glob $G --study <study>                   # default battery (see below)
& $P $S --runs-glob $G --study <study> --pairs modelA:modelB,modelC:modelD
& $P $S --runs-glob $G --study <study> --pairs modelA:baseline1 --holm --out report.md
#   --metric mape|rmse|mae   (default mape)     --alpha 0.05
#   --runs-glob   REQUIRED in practice (default is cwd-relative runs/*/output/results.json)
#   --studies     JSON name map; defaults to _shared/studies.json if present (curated:
#                 unknown notebooks skipped); no map -> auto-named "<notebook-stem>@<span>"
#   --heavy       comma-separated model names treated as baselines by the default battery
```

## How it groups (identical to the aggregator)

It reads the `results_glob` runs, groups by `(notebook, span_start)` into named studies
via `_shared/studies.json` when present (otherwise auto-named), and takes the **newest run per (study, seed)** (so a 14-model re-sweep supersedes the old
13-model run), skips `smoke_test` runs, and drops NaN/None MAPE. Unlike the aggregator it keeps values
**aligned by seed**, so pairs are genuinely paired (seed 42 vs seed 42, etc.). `n` in the
output is the number of seeds present in **both** configs of a pair.

## The default battery (when you don't name `--pairs`)

1. **best config vs runner-up** — is there a *distinguishable* winner, or a cluster tie?
2. **best non-baseline model vs each baseline** (`--heavy` names) — does the proposed
   family actually clear the baselines, or is it inside noise?

This directly answers the paper's two central questions for any study.

## Reading the output

- **Wilcoxon signed-rank p** is the primary test (non-parametric, right for n≈10). The
  **paired-t p** is shown alongside for reference — when they disagree, trust Wilcoxon.
- **median Δ(A−B)**: lower MAPE is better, so a **negative** median diff means the *first*
  model wins. The verdict column states the winner only when p ≤ α.
- Floor value: with all 10 paired diffs the same sign, the exact two-sided Wilcoxon
  p = 2/2¹⁰ = **0.00195 ≈ 0.0020** — that's the smallest p n=10 can produce, and it's
  what a clean sweep returns.

## Integrity rules (non-negotiable)

- **Always report n and the exact p** — never an asterisk alone, never "significant"
  without the number. n≈10 is low power; say so.
- **A non-significant result is a result.** Report "no significant difference (p=0.49)" —
  never drop a tested pair because it came out null. A nominal "win" that tests p=0.49
  must be described as a tie in the manuscript.
- When you test **many** pairs at once (e.g. one model against all others), pass
  `--holm` for Holm-Bonferroni and **disclose** that you did. Without it, say the pairs
  were tested independently.
- Never convert a p-value into a stronger claim than it supports. "Model X is nominally
  best but indistinguishable from three siblings (p>0.4)" is the honest form of "X wins."

## When to run it

- Before writing/keeping any "beats", "outperforms", "ties", "leads by" claim in the
  manuscript — especially the borderline ones (nominal gaps of ~1–2 standard errors).
- Pairs naturally with `/claims-audit`: that skill flags a numeric claim, this one tells
  you whether the underlying gap is statistically real.

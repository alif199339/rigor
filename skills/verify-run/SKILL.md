---
name: verify-run
description: Cross-verify a completed remote/notebook run's results.json (or notebook output) against the project's verified reference numbers before reporting to the user -- checks shapes, leakage, seed count, NaN, futility stops, and regressions. Reference-number location and anchors come from _shared/project_profile.yaml.
---

# /verify-run — scientific-integrity check on a completed run

The last gate before any number reaches the user. Adapted for the `results.json`-based
record that Kaggle runs produce (Kaggle never returns an executed notebook — `results.json`
+ `experiments.md` are the primary record).

## Layer 2 — from `_shared/project_profile.yaml`

`results_glob`, `experiments_log`, `reference_results` (where verified numbers live),
`anchors` (expected parameter counts + the headline finding — an exact-match fingerprint
that the intended pipeline ran).

## Layer 1 — the checklist

1. **Locate the record.** Kaggle: `<sweep_dir>/runs/<run_id>/output/results.json` + the
   `experiments.md` row. Colab: the executed notebook's printed cells.
2. **Shape/config sanity.** Does `results` contain *every* config the notebook should run
   (all ten configs + every heavy baseline)? A missing key = something errored silently
   mid-run — treat as failure, not partial success.
3. **`smoke_test` flag.** If top-level `smoke_test` is `true`, the metrics are **meaningless
   by the notebook's own convention** — never report them as real, never compare to the
   reference table. Say so explicitly.
4. **Futility stops.** For any config with a non-null `futility_stop`, report it plainly
   (which config, epoch, reason: `nan_or_inf` vs `no_progress_vs_trivial_baseline`). A
   legitimate early-abort — flag as reduced-confidence for that entry, don't hide it, don't
   silently drop it from a comparison.
5. **Compare against `reference_results`.** A deviation of more than a point or two of MAPE
   from prior runs *at the same seed/window/epochs* is a **regression to flag**, not to
   accept. But don't treat prior numbers as gospel across a different seed — check the
   `anchors`: parameter counts should match exactly (an exact match confirms the intended
   feature pipeline ran). A wrong param count means the wrong pipeline ran, regardless of
   the metric.
6. **NaN/divergence.** Any `nan` rmse/mae/mape (possible if a futility stop fired before a
   usable checkpoint) is a failed config — report it, never silently omit from a table.
7. **Leakage/methodology guardrails.** Nothing in the remote pipeline should have touched
   the leakage-free design (train-only correlation adjacency; the disclosed legacy scaler
   caveat, not a new leak; the loss function; the ReLU in any learnable-adjacency residual).
   If a code change accompanies a run, check it against these *before* running.
8. **Seed-count disclosure.** Unless the run was an explicit multi-seed sweep, state the
   seed count when reporting — never present a single-seed number as robust/averaged. Where
   the project's headlines are single-seed, say so. **Pair with `/stat-check`** to test
   whether a multi-seed "X beats Y" gap is actually significant before writing it as a win.
9. **Never fabricate or hand-tune.** Report exactly what `results.json`/`experiments.md`
   contain. If a number surprises you, say so and show the raw data.

## Output

A short table/list: config → RMSE/MAE/MAPE/params, any futility stops, the `smoke_test`
state, and one line per comparison against the nearest reference number
(regression/improvement/in-line). If a real (non-smoke) run produces numbers that should
become the new reference, flag that `reference_results` needs updating — don't edit
reference numbers silently.

---
name: run-remote
description: Execute a research notebook unattended on Kaggle's free GPU via the runner (papermill inject -> push -> poll -> download -> parse), then hand off to /verify-run. Paths and the notebook template come from _shared/project_profile.yaml.
---

# /run-remote — dispatch a notebook to Kaggle and report back

Drives the project's `runner.py` end to end for one sweep entry, then hands off to
`/verify-run` for the integrity check.

## Layer 2 — read these from `_shared/project_profile.yaml`

`sweep_dir` (work dir), `runner`, `results_glob`, `experiments_log`,
`reference_notebook_template`, `python_pypdf` (the interpreter with kaggle/papermill),
`gpu_requires_user_ok`. The host project's `CLAUDE.md` wins over these defaults wherever
they conflict.

## Layer 1 — the portable mechanics

### Before running

1. **Confirm the target notebook is Kaggle-enabled**: a papermill `parameters`-tagged
   cell, a path-shim (env var → `/kaggle/input/**` glob → local fallback), a futility gate
   in its training loop, and a `results.json` export cell. The profile's
   `reference_notebook_template` is the reference to copy the pattern from. If the notebook
   lacks these, enabling it is a prerequisite task, not this skill (edit the notebook JSON
   directly via a scratch script if it's too large for NotebookEdit/Read).
2. **Check the sweep entry** in `<sweep_dir>/sweep*.yaml` — right notebook, `SMOKE_TEST`
   true/false, right `dataset_sources`. **Never flip `enable_gpu: true` without the user's
   explicit go-ahead** when `gpu_requires_user_ok` is set — it spends real weekly GPU quota
   (~30 GPU-h, shared). A `SMOKE_TEST: true` / `enable_gpu: false` dry pass is always safe.
3. **First push of a session:** sanity-check auth — `py -3.11 -m kaggle config view` should
   show `auth_method: ACCESS_TOKEN`.

### Running

```powershell
cd "<project_root>/<sweep_dir>"
py -3.11 runner.py --dry-run       # always safe: papermill-inject only, no Kaggle push
py -3.11 runner.py --max-runs 1    # real push+poll+download for the next un-run entry
py -3.11 runner.py --resume        # skip run_ids already "complete" in experiments.md
py -3.11 runner.py --account <name> --sweep <file>.yaml   # multi-account / alt sweep
```

The runner sets `PYTHONUTF8=1` for every `kaggle` subprocess — don't strip it (without it,
pushing any notebook with unicode crashes on Windows with `'charmap' codec can't decode`).
⚠️ **`--resume` collision risk:** `completed_run_ids()` matches run-id *strings* from
`experiments.md` regardless of account/notebook version — a stale same-named row makes it
silently skip a real retry. For retries prefer a **fresh `kernel_slug_prefix`** and run
*without* `--resume`.

While polling, `fit_and_eval` prints per-epoch val RMSE to the Kaggle log (read it live on
long runs) and `[FUTILITY STOP]` lines. A futility stop is a legitimate early-abort of one
diverging config, not a broken run — record it plainly, never hide it.

### After running

1. Read `<sweep_dir>/runs/<run_id>/output/results.json` directly. **Kaggle never returns
   an executed `.ipynb`** (confirmed project behavior) — this file + the `experiments.md`
   row are the record. The source notebook stays the clean parameterized template.
2. On `FAILED`, read `<sweep_dir>/logs/<run_id>_error.log` before concluding root cause.
   (Known transient classes: a Pascal-P100 assignment — pin `machine_shape: NvidiaTeslaT4`;
   a DNS blip on `api.kaggle.com` — retry with a fresh prefix.)
3. **Invoke `/verify-run`** against the `results.json` before reporting any number — never
   relay a run's output as verified fact without that cross-check.

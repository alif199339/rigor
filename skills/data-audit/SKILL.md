---
name: data-audit
description: Dataset fingerprinting, degeneracy detection, and drift verification for the data files and preprocessed bundles experiments consume. Catches the silent killers -- a feature column gone all-constant or all-null after a merge bug, creeping NaNs, a rebuild that changed shape or column order -- BEFORE results inherit them. `fingerprint` records what the data is; `verify` recomputes and diffs (exit 1 on hard drift). Use when building/rebuilding any dataset or bundle, before a sweep, or when results shift unexplainably.
---

# /data-audit — the data bug you haven't noticed yet

The most expensive research bugs are not crashes; they are datasets that silently
stop meaning what you think they mean. A holiday-category column that a dict-key
type mismatch turned all-zero. A stale single-run file averaged into a ten-seed
mean. Neither crashes anything — the numbers just come out subtly wrong. This
skill makes those failures **mechanical to catch**.

The script is `data_audit.py` (stdlib; `.npy` stats need numpy). It never
modifies data.

## The two-phase discipline

```powershell
$env:PYTHONUTF8="1"
$D = "skills/data-audit/data_audit.py"

# 1. When a dataset/bundle is built (or first adopted): record what it IS
python $D fingerprint path\to\bundle\ --strict          # exit 2 if born broken

# 2. Before every consuming run (or after any rebuild): prove it's still that
python $D verify path\to\bundle\ --against path\to\bundle\fingerprint.json
```

- `fingerprint` walks the CSV/.npy/.json files, records shape, per-column null
  counts, distinct counts, min/max, and a per-column content hash — and runs the
  **degeneracy checks immediately** (a first fingerprint of already-broken data
  must shout, not just record): all-null columns, constant columns, >50%-null
  columns, byte-identical duplicate columns, constant/NaN-bearing arrays.
- `verify` recomputes and classifies changes: **hard** (row/shape/dtype change,
  columns added/removed/reordered, nulls increased, a column collapsed to
  constant, new NaNs, file missing, any degeneracy) exit 1; **soft**
  (hash changed but stats hold, nulls *decreased*) reported as warnings.

## When Claude runs this (division of labor)

1. **After building or rebuilding any dataset/bundle** — fingerprint with
   `--strict` and read the warnings before declaring the build done. A constant
   column at build time is a pipeline bug to report, not a note to file.
2. **Before dispatching a sweep** (pairs with `/run-remote`) — `verify` the
   bundle the notebook will consume. Ten GPU-hours on silently-drifted data is
   the failure mode this exists for.
3. **When results shift unexplainably between runs** — `verify` against the
   fingerprint from the last good run; data drift is the first hypothesis to
   kill, not the last.
4. **Adjudication is yours**: the script flags; you decide whether a constant
   column is a bug (a feature that should vary) or by design (a config constant),
   and report that reasoning to the user. Never "fix" data — report to the human.
5. Commit `fingerprint.json` with the project so drift is checkable across
   machines and time.

## Layer-2 config (project_profile.yaml)

```yaml
# --- data-audit (dataset integrity) ---
data_dirs:                       # dirs/files worth fingerprinting, one per bundle
  - path/to/dataset_or_bundle
```

## Integrity rules carried in

- Report-only: the script never rewrites, imputes, or drops anything.
- A degeneracy is a **finding for the human** with the evidence line quoted;
  pair it with `/lab-notebook` (`log <track> --type finding --evidence
  fingerprint.json`).
- Exit codes are honest: hard drift/degeneracy is non-zero — wire it into any
  pre-run gate; never swallow it.

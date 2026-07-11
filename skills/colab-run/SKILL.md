---
name: colab-run
description: Semi-attended notebook execution on Google Colab through a Drive-synced folder -- the agent injects parameters and stages the notebook, the researcher taps "Run all" once (that tap IS the GPU approval), and the agent polls the synced folder for results.json, validates it, and journals it. Use when Kaggle quota is exhausted or a Colab-only runtime (e.g. a specific GPU/TPU) is wanted.
---

# /colab-run — Colab as a second free-GPU backend, honestly

Kaggle has an official headless-execution API; **free Colab does not**, and this skill
deliberately does not automate around that (no browser automation — it's against
Google's ToS and brittle). Instead, everything *around* the one human tap is automated,
and the tap itself doubles as the explicit GPU-approval gate RIGOR requires anyway:

```
agent: inject params -> stage into Drive-synced folder     (dispatch)
you:   open in Colab -> Runtime -> Run all                 (the one tap; = GPU approval)
agent: poll synced folder -> validate results.json -> journal -> /verify-run   (poll)
```

The script is `.claude/skills/colab-run/colab_run.py` (stdlib-only — it implements the
papermill `injected-parameters` convention itself, no papermill needed).

## One-time machine setup

1. Install **Google Drive for Desktop** and note the synced root (e.g. `G:\My Drive`).
2. Create a runs folder inside it, e.g. `G:\My Drive\rigor-runs` — pass it as
   `--sync-dir`. If it isn't directly under the Drive root, set `--drive-subdir` to its
   Drive-relative path so the in-Colab persist path resolves.

## Notebook requirements (agent applies these — same conventions as run-remote)

The notebook needs the run-remote template edits (a `parameters`-tagged cell; a
`results.json` export cell with `float()`-cast metrics) **plus one Colab-persist cell**
(place it last, after the export cell):

```python
# Colab: persist results to the Drive run folder (no-op everywhere else)
try:
    from google.colab import drive           # only exists on Colab
    import os, shutil
    drive.mount("/content/drive")
    os.makedirs(RIGOR_RUN_DIR, exist_ok=True)
    shutil.copy("results.json", os.path.join(RIGOR_RUN_DIR, "results.json"))
    print("RESULTS_PERSISTED ->", RIGOR_RUN_DIR)
except ImportError:
    pass
```

`RIGOR_RUN_DIR` is injected automatically by `dispatch` — never hardcode it.
`dispatch` warns if the notebook lacks the export cell or the persist cell.

## Commands

```powershell
$env:PYTHONUTF8="1"
$C = ".claude/skills/colab-run/colab_run.py"

# 1. agent stages the run (repeat --param per knob; values are JSON-ish literals)
python $C dispatch --notebook "notebooks/my_experiment.ipynb" `
       --sync-dir "G:/My Drive/rigor-runs" --run-id exp1-seed42 `
       --param SEED=42 --param SMOKE_TEST=False

# 2. human: open colab.research.google.com -> Open -> Drive -> rigor-runs/exp1-seed42
#    -> pick GPU runtime -> Run all -> approve the Drive-mount prompt

# 3. agent collects (blocks up to --timeout-min; or `collect` for a one-shot check)
python $C poll --sync-dir "G:/My Drive/rigor-runs" --run-id exp1-seed42 `
       --timeout-min 180 --journal "Kaggle Agent/experiments.md"
```

`poll` tolerates Drive's partial-file syncing (invalid JSON → retry), validates the
schema, prints a summary, and appends a journal row. **Then run `/verify-run`** on the
results before reporting any number — same rule as every backend.

## Integrity rules (same spine as run-remote)

- **The tap is the approval.** The agent never asks the researcher to tap "Run all" on a
  GPU runtime without stating what will run and roughly how long; smoke passes first,
  always. The agent must never attempt to automate the browser step.
- **Smoke-test discipline unchanged:** prove the pipeline locally on CPU before any
  Colab dispatch; a `SMOKE_TEST=True` staging costs the researcher a pointless tap.
- **Session realism:** free Colab evicts idle/long sessions and quota varies by account
  history. `poll`'s timeout exists because a run can die invisibly — on timeout, tell
  the user to check the Colab tab; never silently re-dispatch a GPU run.
- **Credentials:** none touch this skill. Drive sync is the OS-level client the user
  installed; the agent only reads/writes local files.
- Results are the record: `results.json` + the journal row (Kaggle's
  `experiments.md` can be shared via `--journal` so all backends land in one log).

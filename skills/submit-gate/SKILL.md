---
name: submit-gate
description: The pre-submission gate -- one command runs the whole audit battery (bib-audit, claims-audit, cite-check, stat-check, data-audit verify, figure staleness) into a single SUBMISSION_READINESS.md with a READY/NOT-READY verdict, and `freeze` snapshots (sha256) every file the submission's numbers rest on so reviewer questions months later are answered against what was actually submitted. Use before any submission or resubmission ("are we ready to submit?", "run all the checks"), and immediately AFTER submitting ("freeze it").
---

# /submit-gate — one verdict before you submit, one snapshot after

Before every submission the same battery gets run by hand: bibliography audit,
claims audit, citation check, statistics check, data verification, figure
staleness. This skill turns that into **one command with one verdict** — and
adds the step everyone skips: freezing what was submitted, so that when a
reviewer asks about Table 3 in three months (after the sweeps have moved on),
the answer comes from the submitted artifacts, not from whatever the files have
since become.

The script is `submit_gate.py` (stdlib + pyyaml). It runs tools and hashes
files; it never edits anything.

## 1. Compose the gate once per project

Write `gate.yaml` from the project profile (agent does this at first use; keep
it committed):

```yaml
steps:
  - name: bib-audit
    cmd: [python, .claude/skills/bib-audit/bib_audit.py, --bib, latexs/references.bib]
    required: true
  - name: claims-audit
    cmd: [python, .claude/skills/claims-audit/claims_audit.py, --tex, latexs/main.tex,
          --tables, latexs/tables, --results, "sweeps/runs/*/output/results.json"]
    required: true
  - name: cite-check
    cmd: [python, .claude/skills/cite-check/cite_check.py, --tex, latexs/main.tex,
          --bib, latexs/references.bib, --papers, "literature/*/papers.json"]
    required: true
  - name: data-audit
    cmd: [python, .claude/skills/data-audit/data_audit.py, verify, data/bundle,
          --against, data/bundle/fingerprint.json]
    required: false        # only if the project keeps fingerprints
```

## 2. Gate, then freeze

```powershell
$env:PYTHONUTF8="1"
$G = "skills/submit-gate/submit_gate.py"

python $G check --config gate.yaml            # -> SUBMISSION_READINESS.md, exit 1 = NOT ready
# ... submit ...
python $G freeze --label "JOSS-v1" --files "latexs/tables/*.tex" `
    "sweeps/runs/*/output/results.json" latexs/main.pdf latexs/references.bib

# months later, when a reviewer asks about a number:
python $G verify-freeze --against freeze_2026-07-14.json
```

## Division of labor

- **Script**: runs each step, aggregates exit codes and output tails, writes the
  readiness report with a READY / NOT-READY verdict; snapshots and re-verifies
  the frozen files.
- **Agent (you)**: compose `gate.yaml` from the profile; after a NOT-READY, read
  each failing tool's own report and drive the fixes (each tool's SKILL.md rules
  apply); after answering a reviewer months later, run `verify-freeze` FIRST and
  disclose in the response if the artifacts have moved since submission.
- **Human**: the verdict informs, never replaces, their decision to submit.

## Integrity rules carried in

- A FAIL on a required step is a NOT-READY verdict — never argue the report down;
  fix the underlying finding or consciously mark the step `required: false` with
  a committed reason in gate.yaml.
- Freeze **immediately after** submitting, with a `--label` naming the venue and
  round. The freeze file is committed — it's the submission's birth certificate.
- Answering reviewer questions about submitted numbers = `verify-freeze` first,
  always. If files changed, say so in the response rather than silently quoting
  the new values.

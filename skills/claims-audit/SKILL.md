---
name: claims-audit
description: Reconcile every numeric claim in a LaTeX or Markdown manuscript's prose/captions against ground-truth data (aggregator-generated tables + results.json). Flags stale-drift (a table re-swept but the prose kept the old number), orphan numbers (in no table/result), and stale figures (older than the newest run). Emits candidates; never edits the manuscript. Use after a sweep re-run / before submission, paired with the main.tex reshape.
---

# /claims-audit — do the manuscript's numbers still match the data?

Numbers drift. A table gets regenerated after a re-sweep, but a metric quoted in the
abstract or a Discussion sentence keeps its old value. A manual audit of the reference
install found **nine** such mismatches in one manuscript. This mechanizes that check so
it reruns after every sweep.

The script is `.claude/skills/claims-audit/claims_audit.py` (stdlib-only):

```powershell
python .claude\skills\claims-audit\claims_audit.py `
    --tex path\to\main.tex --tables path\to\tables `
    --results "<sweep_dir>\runs\*\output\results.json"
#   --figures <dir>   default: <tex-dir>/figures
#   --studies <json>  study-name map; defaults to _shared/studies.json (else auto-named)
#   --out <path>      default: <tex-dir>/claims_audit_report.md
```

It extracts every numeric literal from the manuscript **prose/captions** (skipping
`\input`-ed table fragments, figure includes, refs/cites/labels, `\pend` placeholders,
and LaTeX layout dims), builds a ground-truth pool from `tables/*.tex` + the aggregated
`results.json` (grouped via `_shared/studies.json` when present, else auto-named), and
classifies each claim. It also runs a **figure-staleness** pass and lists any `\pend` holes.

## When to run

After every sweep lands (the moment tables regenerate is the moment prose goes stale),
and always once more before submission. A dry-run at any time is cheap and safe.

## The three buckets (and how to adjudicate — this is Claude's job, not the script's)

The script emits **candidates**. You read the context column and decide. It never edits
the manuscript.

- **NEAR-MISS** — close to a real value but off beyond rounding. **Prime stale-drift
  suspects** (e.g. prose says `4.12±0.31` but the re-swept table now says `4.05±0.08`).
  Read each: is it a results claim that should track a table? Fix it. Is it a correlation
  coefficient / α / legacy single-run number that merely *happens* to sit near a MAPE?
  Leave it.
- **ORPHAN** — appears in no table/result. Three sub-populations, only one dangerous:
  (a) legitimate **derived** values (a computed gap "0.26–0.35 pp", a sum) — verify the
  derivation; (b) **structural** constants (N=9, 70/15/15, "1.5 years") — fine; (c) a
  **results-like** number (`likely MAPE? = yes`) that should trace to a table but doesn't
  — the fabrication/typo risk. Focus on (c).
- **MATCHED** — equals a ground-truth value within rounding. Not listed individually.

Legacy **inline** tables written directly in the manuscript (hand-typed rather than
`\input`-generated) are audited as claims — their hand-typed cells show
up as NEAR-MISS/ORPHAN against the 10-seed pool. That's correct: those are exactly the
hand-typed numbers most prone to drift. Adjudicate them against their *own* stated source
(e.g. "single-seed, W=168, disclosed as legacy").

## Figure staleness

Every results-derived figure whose file mtime is older than the newest `results.json` is
flagged **STALE** → re-run your aggregation/plotting script. Hand-drawn TikZ figures (those
with a `.tex` twin in `figures/`) are marked N/A (they don't derive from run data). A
figure referenced but absent from `figures/` is flagged **MISSING**.

## Division of labor & integrity

- The script **never edits `main.tex`** — it produces `claims_audit_report.md`. You
  summarize the real problems to the user and propose edits for approval.
- Pair with **`/stat-check`**: claims-audit flags *that* a "X beats Y by Δ" number
  drifted; stat-check tells you whether the underlying gap is even significant, so you
  fix the prose to the honest claim, not just the honest number.
- Never hand-fill a number the pool doesn't contain — if a claim is ORPHAN and should be
  a results value, get it from the table/results.json, not from memory.
- Expect noise (layout numbers slip through, correlation coefficients look MAPE-ish). The
  tool trades false positives for zero false negatives on the dangerous class; the human
  pass is the point, not a failure of the tool.

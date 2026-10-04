---
name: result-audit
description: Reviewer-side internal-consistency screening of submitted PDF papers (single PDF or a conference batch/zip) -- reconcile abstract/conclusion headline numbers against the paper's own body and tables, recompute in-sentence improvement percentages and data splits, and catch impossible statistics (p>1, accuracy>100%, |r|>1, statcheck-style p recomputation, GRIM). Fully offline; flags candidates with page + quote for agent adjudication. Use for conference/journal screening, "check these papers' results for inconsistencies", editorial integrity checks.
---

# /result-audit — does the paper agree with ITSELF?

<!-- rigor:paths -->
> **Paths and names in this file.** Script paths below are written
> `$RIGOR/skills/<skill>/<script>.py`. Set `$RIGOR` to `${CLAUDE_PLUGIN_ROOT}`: Claude Code
> replaces that with an absolute path when it loads this file, so if the braces are gone,
> this is a plugin install and that path is `$RIGOR`. If you can still read the literal
> `${CLAUDE_PLUGIN_ROOT}`, this is a copy-paste install — set `$RIGOR` to `.claude`.
> Skill names are written `/name`; a plugin install namespaces them as `/rigor:name`.

`/claims-audit` reconciles an author's manuscript against their `results.json`.
A reviewer has no ground truth — only the PDF — so this skill checks the one
thing that needs no external data: **internal consistency**. An abstract that
says 95.2% when Table III says 94.8%, an "improves by 20%" no pair of numbers
in the sentence reproduces, a p-value its own test statistic contradicts: these
are verifiable content failures whatever their origin, and unverified numbers
are the classic fingerprint of heavy, unchecked AI drafting. The report states
the facts; attribution is the technical chair's call.

The script is `result_audit.py` (pypdf; scipy optional — without it the
statcheck-style p recomputation is skipped and disclosed). **Fully offline** —
nothing about the submission leaves the machine.

## Run it

```powershell
$env:PYTHONUTF8="1"
python $RIGOR\skills\result-audit\result_audit.py --dir papers\        # batch
python $RIGOR\skills\result-audit\result_audit.py --pdf papers\<submission-id>.pdf
```

Per paper: `<stem>_result_audit.md` (worksheet) + `.json`; `--dir` adds
`result_audit_summary.md`. Seconds per paper — the network-free half of the
screening battery (pair with `/ref-audit` for the references half).

## The seven checks (all emit CANDIDATES, page-tagged and quoted)

| Check | What it catches |
|---|---|
| **NEAR-MISS** | an abstract/conclusion headline number *close* to a body/table value but off beyond its printed precision — the stale/mistyped-number class |
| **ORPHAN** | a results-like headline number appearing **nowhere** in the body — the untraceable-claim class |
| **ARITH** | a same-sentence "improves/reduces by N%" that no pair of the sentence's numbers reproduces (relative *or* percentage-point reading) |
| **SPLIT** | a train/val/test split not summing to ~100 |
| **IMPOSSIBLE** | p > 1 or p = 0, accuracy/percent > 100, \|r\| > 1, negative RMSE/MAE/variance; with scipy: t(df)/F/χ² vs stated p recomputation |
| **GRIM** | a mean unattainable from the stated integer N at the printed precision |
| **XSECTION** | the same metric quoted with irreconcilable values in abstract vs conclusion |

## The adjudication pass (yours — nothing reaches the chair without it)

PDF text extraction is lossy (two-column reflow, mangled tables); the script
deliberately trades false positives for zero misses on the dangerous classes.
For **every** candidate, Read the flagged page(s) of the actual PDF (the Read
tool renders PDFs) and assign:

- **CONFIRMED-INCONSISTENT** — the inconsistency is real in the rendered PDF.
  You MUST quote both conflicting fragments verbatim (e.g. the abstract sentence
  *and* the table cell). No two quotes, no CONFIRMED.
- **EXPLAINED** — both numbers are real but refer to different things (another
  dataset/split/metric variant); say which.
- **EXTRACTION-NOISE** — the "inconsistency" is an artifact of text extraction.

Write `<stem>_adjudication.json` next to the PDF (shared with ref-audit):

```json
{ "result": { "C3": {"decision": "CONFIRMED-INCONSISTENT",
                     "note": "abstract p.1: 'achieves 95.2% accuracy' vs Table III p.5 best row: '94.8'"},
              "C7": {"decision": "EXTRACTION-NOISE", "note": "column reflow merged two rows"} } }
```

For a batch of >10 papers, fan the reading out to read-only Explore sub-agents
(one per handful of papers, each given the worksheet + PDF paths and the rules
above); sub-agents report back, the **main session** writes the adjudication
files — single-writer, as in `/cite-check`.

Then compile the chair workbook:
`python $RIGOR\skills\_shared\panel_compile.py --dir papers\ [--id-map ids.csv]` →
`PANEL_TRIAGE.xlsx`. Only CONFIRMED-INCONSISTENT items appear on the Findings
sheet (as DATA-INCONSISTENCY, with your quotes); everything else stays in the
per-paper worksheets.

## Integrity rules (non-negotiable)

- **Facts, not attribution**: never state or imply that a paper is AI-written.
  The workbook records verifiable failures; the AI-ethics judgment and the
  decision belong to the technical chair.
- Never confirm a candidate from the extracted text alone — the rendered PDF
  page is the evidence, and both sides of the contradiction must be quoted.
- A candidate that turns out EXPLAINED or NOISE is a *result*, not a failure —
  record it; it is what keeps the confirmed findings defensible.
- Report-only: nothing is edited, nothing leaves the machine, outputs stay in
  the papers directory.

---
name: cite-check
description: Content-level citation verification -- does each cited paper actually SAY what the manuscript's sentence claims it says? Pairs every \cite-bearing claim sentence with the cited work's abstract (from lit-review stores) into a worksheet; the agent then judges SUPPORTED / NOT-SUPPORTED / CANT-VERIFY, quoting the source verbatim. Complements bib-audit (existence) with miscitation detection (content). Use before submission, after adding new citations, or when asked "check that my citations support my claims".
---

# /cite-check — catch miscitations, not just fake citations

`bib-audit` proves a cited work **exists**; this skill checks that it **supports the
sentence citing it**. Miscitation — a real paper cited for a claim it doesn't make —
is the most common citation failure in real manuscripts and survives every
existence check.

Two halves, strictly divided:
- **Script** (`cite_check.py`, stdlib, offline): extracts every citation-bearing
  sentence, resolves keys against the `.bib`, attaches the best available
  abstract from lit-review stores → `cite_check_worksheet.md` (+ `.json`).
- **Agent** (you, under the rules below): judges each PAIRED item and writes
  `cite_check_report.md`. The script never judges; you never invent evidence.

## Run the mechanical half

```powershell
$env:PYTHONUTF8="1"
python .claude/skills/cite-check/cite_check.py `
    --tex path\to\main.tex --bib path\to\references.bib `
    --papers "literature/*/papers.json" --out-dir path\to\
```

Exit 1 = a cited key is missing from the `.bib` entirely (fix that first).
Statuses in the worksheet:

| Status | Meaning | Your move |
|---|---|---|
| PAIRED | claim sentence + abstract ready | judge it (below) |
| NO-ABSTRACT | work is in a store but the abstract is withheld | screen its fulltext (`lit-review fulltext`) or the DOI landing page before judging |
| NOT-IN-STORE | cited work not in any provided papers.json | `lit-review lookup --title/--id` it into the store, re-run |
| NO-BIB-ENTRY | `\cite{key}` with no .bib entry | hard error — fix the .bib |

## Judging (the integrity rules)

For each PAIRED item, compare the **claim sentence** against the **abstract** and
assign exactly one verdict:

- **SUPPORTED** — the abstract states or directly entails the claim. You MUST
  quote the supporting sentence(s) verbatim in the report. No quote, no SUPPORTED.
- **NOT-SUPPORTED** — the abstract contradicts the claim, or the claim attributes
  something (a method, a number, a finding) the abstract gives no basis for.
  Quote what the abstract *does* say. This is a **candidate for the human** — never
  edit the manuscript or the citation yourself.
- **CANT-VERIFY** — the claim is about something an abstract wouldn't cover
  (a detail in §4, a dataset size, an implementation choice). Say what part of the
  paper would settle it; suggest `lit-review fulltext` when the PDF is available.

Hard rules:
- An abstract is a summary: **SUPPORTED via abstract** is the strongest claim you
  may make from it. If the judgment matters (a load-bearing citation), verify
  against the fulltext before final wording.
- Never judge from model memory of the paper. The worksheet's abstract (or a
  retrieved fulltext page) is the only admissible evidence — if it isn't there,
  the verdict is CANT-VERIFY, not what you remember.
- Background/perfunctory cites ("graph networks have been applied widely [CITE]")
  still get judged — over-claiming in related-work sections is where miscitations live.

## Sub-agent workflow (recommended for >20 citation instances)

Adjudication is context-heavy (many abstracts) and embarrassingly parallel. Spawn
one or more **read-only** sub-agents (Claude Code type: `Explore`), splitting the
worksheet by key range:

> Read `cite_check_worksheet.md` sections `<A>`–`<M>`. For each numbered claim
> under each key, judge SUPPORTED / NOT-SUPPORTED / CANT-VERIFY against the quoted
> abstract ONLY (never from memory of the paper). SUPPORTED requires quoting the
> abstract sentence that supports it. Return: key, claim number, verdict, quote or
> reason. Modify nothing.

The main session merges the returns into `cite_check_report.md`, ranks
NOT-SUPPORTED first, and hands the human a candidates list. Sub-agents never
write files; the main session owns the report.

## Where the abstracts come from

Only from lit-review stores (`papers.json`) — this skill does no network calls, so
every abstract already carries lit-review's provenance. If coverage is poor, run
`lit-review lookup/enrich` first; the two-skill loop keeps "grounded by
construction" intact.

---
name: bib-audit
description: Verify every entry in a BibTeX file against Semantic Scholar + Crossref before submission -- catch hallucinated, drifted, or mistyped citations (wrong year/title/venue, missing DOI, unresolvable works). Produces a report that proposes field-by-field fixes; never auto-edits the .bib. Use for `references.bib`/`.bib` audits, "check my citations", pre-submission bibliography QA.
---

# /bib-audit — verify a bibliography against real records, propose fixes, never auto-edit

`/lit-review` guarantees *new* citations are real. This closes the other hole: a `.bib`
that predates the skill (or was hand-edited) can still carry a wrong year, a drifted
title, a missing DOI, or an entry that resolves to nothing. `/bib-audit` checks every
entry against the **live Semantic Scholar + Crossref APIs** and writes a report you
review before touching the file.

The script is `.claude/skills/bib-audit/bib_audit.py` (stdlib-only, any Python 3.10+;
use `py -3.11` or the project smoke venv).

## The non-negotiable rule

**The script reports and proposes — it NEVER rewrites the `.bib`.** Every suggested fix
is a candidate. You (with the user) adjudicate each one and the user approves before any
edit. This mirrors `/lit-review`'s integrity stance: no citation metadata changes without
a real, resolved source behind it.

## Run it

```powershell
$env:PYTHONUTF8="1"          # Windows: abstracts/venues are unicode-heavy
$B = "path/to/references.bib"
python .claude/skills/bib-audit/bib_audit.py --bib $B --mailto <your-email>
#   --out   <path>          default: <bib-dir>/bib_audit_report.md
#   --only  key1,key2       audit just these entry keys (fast re-check after a fix)
#   --mailto you@x.com      Crossref polite-pool contact (recommended; else generic UA)
```

`S2_API_KEY` (user env var) is picked up automatically and enforces 1 req/s; keyless
still works but throttles. Crossref is a separate polite pool keyed by `--mailto`. A
~15-entry bib takes ~1 minute. Output: `bib_audit_report.md` — a summary table (ranked
worst-first) + per-entry detail with diffs and suggested BibTeX lines.

## The five verdicts (and how to act on each)

| Verdict | Meaning | Your action |
|---|---|---|
| **MISMATCH** | A record was found but a field conflicts (title drift, or a >2-yr year gap). | **Read the detail.** Often a *different edition/record* was title-matched (a reprint, a review) — the note says so. Verify identity; apply a fix only if it's genuinely the same work. |
| **NOT-FOUND** | No confident match in S2 or Crossref. | The paper may be real but mistitled in the `.bib` (see the "closest guess"), or genuinely obscure. Verify it exists; fix the title so it resolves. |
| **UNVERIFIABLE** | Unpublished/submitted or otherwise uncheckable. | Expected for companion/submitted papers. Re-audit after publication; leave as-is meanwhile. |
| **NON-PAPER-OK** | Dataset/standard/software (`@misc` + live URL). | Fine. The URL resolved. No paper record is expected. |
| **VERIFIED** | Matches a real record. | Nothing required. If a `doi = {...}` / `eprint = {...}` line is suggested, adding it strengthens the entry — still the user's call. |

## Interpreting year diffs (important — avoids false alarms)

Semantic Scholar's `year` is the **earliest version** it knows, which for a conference
paper is usually the **arXiv preprint**, a year *before* the proceedings. So an ICLR/IJCAI
paper legitimately shows e.g. "2017 vs 2016". The tool already handles this:

- **≤2-year gap** → reported as an informational *note* ("typical preprint-vs-publication
  offset; the .bib value is often the correct published year"), and the entry stays
  **VERIFIED**. Do **not** blindly change the `.bib` year to match S2 — confirm which
  version is cited (the published year is usually right).
- **>2-year gap** on a title-only match → flagged **MISMATCH** with a "likely a different
  edition/record" note and **no auto-suggested fix**. This is the wrong-record signal
  (e.g. a 5th-edition textbook matching a 1972 journal review). Verify identity by hand.

DOI-based lookups are authoritative; year suggestions there are safe to trust.

## What Claude does with the report (division of labor)

The script does the mechanical lookups; **you do the semantic adjudication**:

1. Read `bib_audit_report.md`. Summarize the MISMATCH/NOT-FOUND entries to the user in
   plain language — which are real problems vs. preprint-year noise vs. wrong-record
   title matches.
2. For each real problem, propose the concrete `.bib` edit (the report's suggested lines
   are your starting point), but **present them for approval** — do not edit
   `references.bib` unprompted. Enrichment-only DOIs (the VERIFIED rows) can be offered as
   a batch "want me to add the missing DOIs?" — still the user's yes.
3. For NOT-FOUND with a plausible "closest guess", cross-check with `/lit-review`'s
   `lookup --title` before proposing a title rewrite — that confirms the real record
   exists and gives you its exact title/DOI.

## Integrity rules carried in

- Never auto-edit `references.bib` or any `.bib`. Report → user approves → then edit.
- Never invent a DOI/venue/year to "fill a gap" — only propose values the API returned.
- Report citation counts, years, DOIs exactly as returned. Preprint-year offsets are
  disclosed, not silently reconciled.
- S2 ≥1.0 s between calls (the script's `S2_SLEEP=1.2` complies); Crossref polite pool via
  `--mailto`. Windows needs `PYTHONUTF8=1`.

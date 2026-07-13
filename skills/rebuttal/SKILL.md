---
name: rebuttal
description: Reviewer-response tracking with mechanically verified change-claims. Imports/collects reviewer comments into a checklist, records each response with the files it claims to have changed, and `check` verifies every "we have revised..." claim against the actual revision diff (exit 1 on claimed-but-not-changed). Compiles the response letter. Use when reviews arrive (journal, conference, JOSS review issue), while drafting responses, and before submitting any revision + response letter.
---

# /rebuttal — never say "we revised it" when the diff says you didn't

The classic rebuttal failure: the response letter says *"we have revised Section 3
accordingly"* and the revision diff shows nothing of the kind. Reviewers notice, and
it reads as bad faith even when it was an honest oversight across a three-month
revision. This skill makes every change-claim **mechanically checkable** before the
letter ships.

The script is `rebuttal.py` (stdlib). One `--dir` per review round
(e.g. `rebuttal/round1/`).

## Workflow

```powershell
$env:PYTHONUTF8="1"
$R = "skills/rebuttal/rebuttal.py"
$D = "rebuttal/round1"

python $R --dir $D init
python $R --dir $D import --file reviews_r1.txt --reviewer R1   # auto-splits comments
python $R --dir $D import --file reviews_r2.txt --reviewer R2
python $R --dir $D status                                       # the checklist

# for each comment, record the response AND what it claims changed
python $R --dir $D respond R1.3 --action change `
    --text "We agree; the claim was overstated. Revised to ..." `
    --anchors main.tex --quote "directional but at the edge of significance"
python $R --dir $D respond R2.1 --action clarify --text "The margin is defined in ..."

# before shipping: verify every change-claim against the real diff
git diff submitted-v1..HEAD -- '*.tex' > revision.patch
python $R --dir $D check --diff-file revision.patch --manuscript main.tex
python $R --dir $D compile                                      # -> RESPONSE.md
```

## The three response actions

| Action | Meaning | `check` verifies |
|---|---|---|
| `change` | the manuscript was changed | every `--anchors` file appears in the diff; the `--quote` appears in added lines (or the current manuscript) |
| `clarify` | answered in the letter, no manuscript change | nothing (by design — but say WHY no change was needed) |
| `decline` | respectfully disagreeing | nothing (the response text carries the argument) |

`check` exits 1 on: an unanswered comment, a `change` whose anchor file isn't in
the diff, a quote found nowhere, or a `change` with nothing verifiable recorded.

## Division of labor

- **Script**: comment bookkeeping, the checklist, diff verification, letter assembly.
- **Agent (you)**: draft the responses — grounded in what was *actually* done (read
  the diff first, then write the claim, not the reverse); pick honest actions
  (`clarify` is not a euphemism for "ignored"); run `check` before `compile` and
  fix failures by **changing the manuscript or the claim**, never by weakening the
  anchor.
- **Human**: approves the letter and every manuscript edit, as always.

## Integrity rules carried in

- A `change` response records its evidence (`--anchors`/`--quote`) at write time —
  a response that can't be verified draws a warning immediately, not at ship time.
- `check` failures are fixed by aligning reality and claim — either finish the
  edit or soften the response — never by deleting the anchor.
- Pair with `/claims-audit` after revisions: reviewer-driven edits are where fresh
  stale-number drift comes from.
- Numbers quoted in responses obey the same rules as the manuscript: from tables /
  stat-check output, never typed from memory.

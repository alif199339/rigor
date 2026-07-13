---
name: lab-notebook
description: Append-only, cross-session lab notebook for long investigations that run on multiple parallel tracks. Logs grounded progress/finding/blocker/decision entries per track into entries.jsonl, prints a session-start status digest, and compiles everything into NOTEBOOK.md; sub-agent workflows re-verify findings against their evidence (audit) and write a citation-checked coherent narrative (narrate + check-narrative). Use at the START of any session continuing a multi-session investigation ("where were we", "resume the investigation", "status"), whenever a result/blocker/decision lands mid-session, and at session end ("log progress", "update the notebook", "compile the notebook", "write the story so far").
---

# /lab-notebook — remember an investigation across sessions, without trusting memory

Long investigations outlive a single session and fan out into parallel tracks
(parsing track, data-quality track, modelling track, writing track…). Chat history dies at
compaction; human memory of "which track was blocked and why" dies faster. This skill
keeps a **machine-readable, append-only notebook** per investigation: every session
reads the digest at start, appends grounded entries as work lands, and recompiles the
report at the end. The notebook — not the conversation — is the source of truth for
"where were we".

The script is `.claude/skills/lab-notebook/notebook.py` (stdlib-only, any Python 3.10+).

## The non-negotiable rule

**The log is append-only. Nothing is ever edited or deleted.** A wrong entry is fixed
by a new entry of `--type correction` with `--refs <old-id>`. A `finding` entry must
name the artifact that shows it (`--evidence path1,path2` — a CSV, figure, log, report);
a finding with no evidence is an assertion, and the script warns. Numbers quoted in an
entry come from the artifact, not from memory. This mirrors a paper lab notebook:
provenance you can defend, not a wiki you can rewrite.

## Layer-2 config (project_profile.yaml)

```yaml
# --- lab-notebook (cross-session investigation tracking) ---
notebook_dir: projects/<paper>/notebook     # one dir per investigation
# a second investigation = a second dir; pass it explicitly with --dir
```

The script takes `--dir` explicitly (it reads no YAML). Claude resolves `notebook_dir`
from the profile and passes it. If the profile has no `notebook_dir` yet, add one when
you `init`.

## Run it

```powershell
$env:PYTHONUTF8="1"                              # Windows: entry text is unicode-heavy
$NB = "projects/my_paper/notebook"           # from project_profile.yaml notebook_dir

# once, when an investigation starts
python .claude/skills/lab-notebook/notebook.py --dir $NB init --name "my-investigation" --plan docs/PLAN.md
python .claude/skills/lab-notebook/notebook.py --dir $NB track-add 1A "Parse the X blocks"
python .claude/skills/lab-notebook/notebook.py --dir $NB track-add 2A "Diagnostics" --depends 1A

# every session
python .claude/skills/lab-notebook/notebook.py --dir $NB status          # session start: read this FIRST
python .claude/skills/lab-notebook/notebook.py --dir $NB log 1A --type finding --text "..." --evidence outputs/t1.csv
python .claude/skills/lab-notebook/notebook.py --dir $NB track-set 1A --status done --note "gate passed"
python .claude/skills/lab-notebook/notebook.py --dir $NB compile         # session end -> NOTEBOOK.md
```

## Entry types (pick the honest one)

| Type | Meaning | Evidence |
|---|---|---|
| `progress` | Work done, no claim about the world ("wrote the parser, runs clean"). | optional |
| `finding` | A result — a number, a pattern, a confirmed/refuted prediction. | **expected** (script warns without it) |
| `blocker` | The track cannot proceed and why. Pair with `track-set <id> --status blocked`. | optional |
| `decision` | A choice that shapes later work, and the why ("dropped IBTrACS, using dates from X"). | optional |
| `correction` | Supersedes an earlier entry; point at it with `--refs <id>`. | as needed |
| `next` | The concrete first action for the next session on this track. | — |

Track statuses: `pending → active → done`, with `blocked` (must have a blocker entry
saying why) and `dropped` (must have a decision entry saying why) as exits.

## What Claude does with it (division of labor)

The script stores and renders; **you keep it honest and current**:

1. **Session start** — run `status` before re-deriving anything from chat history or
   re-reading big plan docs. It lists every track, its last entry, open blockers, and
   queued next-steps. Trust it over your memory of previous sessions.
2. **Mid-session** — log entries **when things land, not in a batch at the end**
   (a compaction can eat an unlogged result). One entry per meaningful event; findings
   carry evidence paths and exact numbers from the artifact.
3. **Track hygiene** — when starting work on a track, `track-set <id> --status active`;
   when its acceptance gate passes, `done`. Before ending the session, write a `next`
   entry on every track you touched (the first command/file/check for next time).
4. **Session end** — `compile`, and tell the user what changed this session in plain
   language. NOTEBOOK.md is regenerated from the log each time; never hand-edit it.
5. **New investigation** — new `--dir`, `init --plan <plan doc>`, one `track-add` per
   work stream in the plan. Keep track ids short and stable (they're typed often).

## Sub-agent workflows (when the log outgrows the session)

Two jobs benefit from a **fresh, isolated sub-agent context** (in Claude Code: the
Agent tool); both keep the script as the truth layer. One rule binds every sub-agent:
**sub-agents never write the notebook.** Entry ids are assigned read-then-append, so
a second concurrent writer can mint duplicate ids — sub-agents return reports, and
only the main session logs entries.

### 1. `audit` — re-verify findings against their evidence

The script checks that evidence paths *exist*; it cannot check that the artifact
still *shows the quoted number*. Before compiling anything someone will rely on,
spawn a **read-only** sub-agent (Claude Code type: `Explore`) with:

> Read `<notebook_dir>/entries.jsonl`. For every entry with `"type": "finding"`,
> open each path in `"evidence"` and check the numbers/claims in `"text"` against
> the artifact. Do the same for `correction` entries that quote numbers — a numeric
> correction with no evidence of its own is an unverifiable assertion; flag it.
> Return one line per audited entry: CONFIRMED / MISMATCH (say what the artifact
> actually shows) / UNVERIFIABLE (artifact missing or claim not derivable from it).
> Modify nothing.

The main session eyeballs the report and logs each real MISMATCH as a `correction`
with `--refs`. This is claims-audit's philosophy turned inward on the notebook.
Why a sub-agent: evidence artifacts are big (CSVs, logs, figures); reading them all
inline bloats the main context — the exact failure this skill exists to prevent.

### 2. `narrate` — compile the single coherent story

`compile` produces mechanical chronology. When the investigation must read as one
story (supervisor update, methods section, thesis chapter), spawn a sub-agent given
ONLY the notebook — a fresh context cannot remember the sessions, so if the story
can't be written from the notebook alone, the notebook is incomplete, and the
sub-agent's gap list is itself valuable output:

> Read `NOTEBOOK.md` and `entries.jsonl` (and the plan doc if listed). Write the
> investigation as a story: the hypothesis; what each track tried; dead ends and
> why they were abandoned (decisions/blockers); the surviving chain of evidence;
> where things stand now. EVERY factual claim cites entry ids as `#N`. Numbers come
> only from entries — never invented, never "improved". End with a list of what the
> notebook does NOT record that the story needed (the gap list).

Save the result as `NARRATIVE.md` beside `NOTEBOOK.md`, then run the mechanical
guardrail — it fails on citations of nonexistent entries, warns when a superseded
entry is quoted instead of its correction, and lists findings the story never used:

```powershell
python .claude/skills/lab-notebook/notebook.py --dir $NB check-narrative $NB/NARRATIVE.md
```

Fix and re-check until clean before sharing the narrative.

### What does NOT need a sub-agent

- **Session-start recap** — `status` already is the digest; read it directly.
- **Mid-session logging** — only the main session knows what just landed.
- **Parallel tracks** — dispatching independent tracks to parallel sub-agents (one
  per track, worktree-isolated if they touch code) is fine and matches the track
  model; they hand results back, and the main session writes the entries
  (single-writer rule above).

## Integrity rules carried in

- Append-only: no entry is ever edited or deleted; corrections are new entries.
- A `finding` cites its artifact via `--evidence`; the script warns on missing paths
  and on evidence-free findings. Don't log numbers you can't point at.
- `NOTEBOOK.md` is generated output — regenerate, never hand-edit; `entries.jsonl` is
  the record.
- The notebook dir belongs to the project (commit it with the repo); the skill folder
  carries no state, so reinstalling RIGOR never touches your logs.
- Windows needs `PYTHONUTF8=1`.

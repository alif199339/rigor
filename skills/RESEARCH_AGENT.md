# RIGOR — Research Integrity Guardrails for Open Research

> **VERSION: 1.2** · This folder (`skills/`, installed as `.claude/skills/`) is the whole
> agent. Copy it into any project's `.claude/` directory and it works there — no edits to
> that project's `CLAUDE.md`, and no secrets travel with it. This file is the manifest;
> it is **not** a skill (no `SKILL.md`), so Claude Code's skill discovery ignores it.

## What RIGOR is

A set of grounded, integrity-first research skills for [Claude Code](https://claude.com/claude-code)
(or any agent harness that reads skill folders). Each is a `skills/<name>/` folder with a
`SKILL.md` (judgment + rules for the agent) and one stdlib-first Python script (the
mechanics):

| Skill | Does | Needs |
|---|---|---|
| **lit-review** | Grounded literature discovery from Semantic Scholar — real DOIs/PDFs only, never model-memory citations. Commands: `search`(+`--bulk`), `lookup`, `snowball`, `recommend`, `contexts`, `enrich`(OpenAlex), `refresh`, `report`, `pdfs`, `fulltext`. | `S2_API_KEY`; pypdf for `fulltext` |
| **bib-audit** | Verify every `.bib` entry against Semantic Scholar + Crossref; propose fixes, never auto-edit. | `S2_API_KEY`, a contact email |
| **claims-audit** | Reconcile manuscript numbers vs table/results ground truth; flag stale-drift, orphans, stale figures. | results.json + generated tables |
| **stat-check** | Paired-by-seed Wilcoxon/t for "X beats Y" claims from multi-seed runs; exact p-values, optional Holm. | scipy |
| **topic-watch** | Re-run a collection's recorded queries, diff for new papers. Manual only. | `S2_API_KEY`; lit-review present |
| **run-remote** | Dispatch a notebook to Kaggle GPU via the runner (push→poll→download→parse). | Kaggle token, a sweep work dir |
| **colab-run** | Semi-attended Colab backend via a Drive-synced folder (inject→stage→one-tap run→poll→journal); the tap doubles as GPU approval. | Google Drive for Desktop |
| **verify-run** | Scientific-integrity checklist on a completed run's results.json. | the profile's `reference_results` |

Integrity is the through-line: a citation must exist in the API-returned `papers.json`
before it may be used; audit skills *report and propose*, never auto-edit; stat-check
reports exact p-values and n (a non-significant result is a result); remote GPU spending
never happens without the user's explicit yes.

## Two-layer design (why it's portable)

- **Layer 1 — portable mechanics** live in each `SKILL.md` + script: how to query S2, how
  to push a Kaggle kernel, the audit procedures. No absolute paths, no project names, no
  reference numbers.
- **Layer 2 — the project profile** (`_shared/project_profile.yaml`, created per install
  from `project_profile.example.yaml`) holds everything project-specific: manuscript path,
  results glob, interpreters, reference-number location, safety flags. The agent reads the
  profile and wires its values into CLI arguments — the scripts themselves stay generic.
  Study naming for results grouping is likewise Layer-2: an optional
  `_shared/studies.json` map (`"notebook.ipynb::span_start" -> "study_name"`); without
  it, studies are auto-named `<notebook-stem>@<span>`.
- `_shared/` has no `SKILL.md`, so skill discovery ignores it — a safe carrier for
  shared config.

## One-time machine setup (per machine, not per project)

Secrets and interpreters are machine-level, so copying the folder never carries them:

```powershell
# 1. Semantic Scholar key (lit-review, bib-audit, topic-watch) -- limit: 1 request/s
setx S2_API_KEY "<your-key>"        # free at https://www.semanticscholar.org/product/api

# 2. Kaggle bearer token (run-remote only) -- the newer KGAT_... scheme, NOT kaggle.json
#    save it to  ~\.kaggle\access_token  (plain text, no trailing newline)

# 3. Python deps (any 3.10+; split across envs is fine -- see the profile keys)
python -m pip install pypdf         # fulltext
python -m pip install scipy         # stat-check
python -m pip install kaggle papermill pyyaml   # run-remote only

# 4. Windows: UTF-8 or cp1252 consoles crash on unicode-heavy abstracts
$env:PYTHONUTF8 = "1"
```

## Per-project onboarding

Install: copy `skills/` → `<project>/.claude/skills/` (merge if one exists), open a fresh
agent session, and say: *"Read `.claude/skills/RESEARCH_AGENT.md` and onboard this
project."* The agent then:

1. Checks for `_shared/project_profile.yaml` — if present, done.
2. If missing, scans the repo: the manuscript (`*.tex` with `\documentclass`), a `.bib`,
   a results format (`**/results.json`), a remote-run work dir (`runner.py` +
   `sweep*.yaml`), the literature dir.
3. Asks only what it can't infer (which interpreter has scipy; where verified reference
   numbers live; the polite-pool contact email).
4. Writes `_shared/project_profile.yaml` from the example, confirms machine deps, and
   reports what it set.

## No-clash guarantees for the host project

1. **Skill-name collisions** are the only real risk. Before copying, check the target's
   `.claude/skills/` for same-named folders; if one exists, rename the incoming folder AND
   its `SKILL.md` `name:` field (discovery keys on that field).
2. **The host `CLAUDE.md` is never written by the agent** — it's read as context, and its
   instructions **win** over skill defaults wherever they conflict.
3. **Namespaced outputs only**: `literature/<slug>/`, the profile file, audit reports next
   to the manuscript, and (for run-remote) the profile's sweep work dir.
4. **Credentials never travel** — they're machine-level (env var + `~/.kaggle*`).

## Where runner.py lives (read before "relocating" it)

`templates/runner.py` self-locates via `Path(__file__).resolve().parent` and resolves
`runs/`, `experiments.md`, `accounts.yaml`, and `sweep*.yaml` relative to its own
directory. That makes it portable **by co-location**: drop a copy into a project's sweep
work dir next to a `sweep.yaml` and it just works. Do **not** place it inside the skills
folder — it would look for its work files there. `run-remote`'s onboarding scaffolds a
work dir and drops the template in on first use; the profile's `runner` key points at it.

## Changelog

- **v1.2** — New **colab-run** skill: Google Colab as a second free-GPU backend via a
  Drive-synced folder. Free Colab has no headless-execution API (and RIGOR does not
  automate around platform terms), so the design is semi-attended: the agent injects
  parameters (papermill convention, stdlib-only) and stages the notebook; the researcher
  taps "Run all" once (the tap = the GPU-approval gate); the agent polls the synced
  folder, validates results.json, and journals it. +7 offline tests.
- **v1.1** — Study naming externalized to `_shared/studies.json` (scripts fully generic);
  `--heavy` baseline names exposed on stat-check; public packaging (README, tests, CI,
  JOSS paper).
- **v1.0** — Initial packaging: lit-review (`fulltext`, OpenAlex `enrich`, `refresh`,
  `contexts`, `bulk`), bib-audit, claims-audit, stat-check, topic-watch (manual),
  two-layer run-remote/verify-run, this manifest + `_shared` profile.

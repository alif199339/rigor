# RIGOR — Research Integrity Guardrails for Open Research

> *An integrity-first agent for the full research workflow — grounded literature,
> verified experiments, honest statistics, auditable manuscripts.*
>
> **VERSION: 1.10** · This folder (`skills/`) is the whole agent, and it installs two ways:
> as a **Claude Code plugin** (`/plugin marketplace add alif199339/rigor`, then
> `/plugin install rigor@rigor` — skills arrive namespaced as `/rigor:<name>`), or by
> **copying it into any project's `.claude/` directory** as `.claude/skills/` (skills are
> then plain `/<name>`). Either way: no edits to that project's `CLAUDE.md`, and no secrets
> travel with it. Each `SKILL.md` resolves its own script path for both installs — see the
> `<!-- rigor:paths -->` note it carries. This file is the manifest; it is **not** a skill
> (no `SKILL.md`), so Claude Code's skill discovery ignores it.

## What RIGOR is

A set of grounded, integrity-first research skills for [Claude Code](https://claude.com/claude-code)
(or any agent harness that reads skill folders). Each is a `skills/<name>/` folder with a
`SKILL.md` (judgment + rules for the agent) and one stdlib-first Python script (the
mechanics):

| Skill | Does | Needs |
|---|---|---|
| **lit-review** | Grounded literature discovery from Semantic Scholar — real DOIs/PDFs only, never model-memory citations. Commands: `search`(+`--bulk`), `lookup`, `snowball`, `recommend`, `contexts`, `enrich`(OpenAlex), `refresh`, `report` (rich entries; `--focus` tiers the collection by relevance to *your* project), `pdfs`, `fulltext`. | `S2_API_KEY`; pypdf for `fulltext` |
| **bib-audit** | Verify every `.bib` entry against Semantic Scholar + Crossref; propose fixes, never auto-edit. | `S2_API_KEY`, a contact email |
| **claims-audit** | Reconcile manuscript numbers vs table/results ground truth; flag stale-drift, orphans, stale figures. | results.json + generated tables |
| **stat-check** | Paired-by-seed Wilcoxon/t for "X beats Y" claims from multi-seed runs; exact p-values, optional Holm. | scipy |
| **topic-watch** | Re-run a collection's recorded queries, diff for new papers. Manual only. | `S2_API_KEY`; lit-review present |
| **pi-scout** | Prospective-PI discovery from YOUR papers: harvest recent citing/related papers, batch-fetch author metrics, score PI-likelihood (warm leads = authors who cite you), shortlist; the agent then web-verifies position (Asst-Prof+), university QS/THE tier + country, and funding signals, and writes per-PI dossiers with an entry-odds audit. Commands: `seed`, `harvest`, `authors`, `shortlist`, `dossier`, `status`. | `S2_API_KEY`; lit-review present; web search for verification |
| **run-remote** | Dispatch a notebook to Kaggle GPU via the runner (push→poll→download→parse). | Kaggle token, a sweep work dir |
| **colab-run** | Semi-attended Colab fallback via a Drive-synced folder (inject→stage→one-tap run→poll→journal); the tap doubles as GPU approval. Field-verified. Kaggle is the recommended headless default. | Google Drive for Desktop |
| **verify-run** | Scientific-integrity checklist on a completed run's results.json. | the profile's `reference_results` |
| **lab-notebook** | Append-only cross-session investigation log: per-track grounded entries (findings cite evidence), session-start digest, compiled NOTEBOOK.md with superseded-entry markers; sub-agent `audit`/`narrate` workflows + `check-narrative` citation guardrail. | the profile's `notebook_dir` |
| **cite-check** | Content-level citation verification: pairs every \cite-bearing claim sentence with the cited work's abstract (lit-review stores only) into a worksheet; agent judges SUPPORTED/NOT-SUPPORTED/CANT-VERIFY quoting the source. | a lit-review store |
| **data-audit** | Dataset fingerprint + degeneracy (constant/all-null/dup columns, NaNs) + drift verify (exit 1) for the data experiments consume. | numpy for .npy stats |
| **ref-audit** | Reviewer-side reference verification from submitted **PDFs** (single or conference batch/zip): extract + parse each reference list, verify via bib-audit's engine, flag HALLUCINATED / retracted / drifted citations; checkpointed batch + `panel_compile.py` → PANEL_TRIAGE.xlsx for the chair. | pypdf, `S2_API_KEY`, bib-audit alongside |
| **result-audit** | Reviewer-side internal-consistency screening of submitted **PDFs**: abstract/conclusion numbers vs the paper's own body/tables, in-sentence % recomputation, impossible statistics (p>1, acc>100%, statcheck-style, GRIM), cross-section contradictions; fully offline, candidates for agent adjudication. | pypdf; scipy optional |
| **rebuttal** | Reviewer comments → responses → `check` verifies every change-claim against the real revision diff; compiles RESPONSE.md. | a diff of the revision |
| **submit-gate** | Runs the audit battery from gate.yaml → SUBMISSION_READINESS.md (READY/NOT-READY); `freeze`/`verify-freeze` sha256-snapshot the submitted artifacts. | pyyaml; a gate.yaml |

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

- **v1.10** — **RIGOR installs as a Claude Code plugin.** New `.claude-plugin/plugin.json`
  and `.claude-plugin/marketplace.json` make this repository both a plugin and a one-entry
  marketplace, so a project gets all sixteen skills with
  `/plugin marketplace add alif199339/rigor` + `/plugin install rigor@rigor` instead of a
  `cp -r`, and receives updates from the marketplace. **No skill changed behaviour.** What
  did change is path resolution: a plugin's files live outside the project, so the
  `.claude/skills/...` script paths hardcoded in ten `SKILL.md` files could not resolve
  there. Every `SKILL.md` now carries a `<!-- rigor:paths -->` note defining `$RIGOR` —
  `${CLAUDE_PLUGIN_ROOT}`, which Claude Code substitutes with an absolute path when it
  loads the file, or `.claude` when those braces are still literal — and all 34 script
  paths are written `$RIGOR/skills/<skill>/<script>.py`. Both branches were verified by
  loading the skills each way and reading back the path the agent resolved. Three
  pre-existing inconsistencies were standardised in passing (`skills/...` with no prefix in
  data-audit, rebuttal and submit-gate, which only resolved when the cwd happened to be the
  install root, plus Windows-backslash variants). **One documented exception:**
  `submit-gate`'s `gate.yaml` example keeps literal paths, because that file is read by
  `submit_gate.py` and its `cmd` arrays are executed directly, so no variable is expanded
  there — the skill now says so explicitly. New offline `tests/test_plugin_manifest.py`
  (10 tests) pins the manifests, keeps `plugin.json`'s version and this file's VERSION from
  drifting, and fails if a hardcoded install path or either documented footgun (an
  entry-level `skills` key, a top-level `bin/`) reappears. The copy-paste install is
  unchanged and still supported.

- **v1.9** — **Two new reviewer-side skills (16 total): ref-audit + result-audit**, the
  first skills whose input is a submitted **PDF** rather than the author's sources —
  built for editorial/program-committee screening (e.g. a conference's accepted-paper
  batch delivered as a zip). **ref-audit** extracts and parses each
  paper's reference list from the PDF text (IEEE numbered grammar first,
  author-start fallback, sequential-marker acceptance so inline brackets can't
  derail the split) and verifies every entry through bib-audit's engine (imported,
  not forked) — S2 + Crossref + Retraction Watch — flagging hallucinated
  (NOT-FOUND), retracted, and drifted citations; a printed DOI/arXiv ID that
  resolves nowhere is called out as the hardest fabrication signal; per-entry JSON
  checkpointing makes 50-paper batches interruptible. **result-audit** is the
  fully-offline half: seven internal-consistency checks against the paper's own
  text (headline-vs-body NEAR-MISS/ORPHAN, same-sentence % recomputation,
  train/val/test splits, impossible statistics incl. statcheck-style t/F/χ²→p
  recomputation when scipy is present, GRIM, abstract-vs-conclusion
  contradictions). Both follow the cite-check division: scripts emit page-tagged,
  quoted CANDIDATES; the agent adjudicates (CONFIRM/EXCLUDE for refs,
  CONFIRMED-INCONSISTENT/EXPLAINED/EXTRACTION-NOISE for results) into per-paper
  adjudication JSONs; `_shared/panel_compile.py` then merges everything into
  **PANEL_TRIAGE.xlsx** (openpyxl, CSV fallback) — one row per exact finding
  (`Ref [17]`, the two conflicting quotes) keyed by submission ID, plus per-paper
  counts. Framing is factual-evidence-only by design: no AI-attribution scoring
  anywhere; confidentiality rule documented (only cited-work title/DOI strings are
  sent to APIs — the submission's own content never leaves the machine).
  **Field test:** a real 314-paper conference screening batch (5,294 references
  parsed; 297 result-audit candidates, every one adjudicated — 15 confirmed
  inconsistencies across 9 papers, the rest explained or extraction noise), reported in
  aggregate only. Also in this release: two **lit-review** `fulltext` crash fixes found
  on a 250-PDF extraction run (`PdfReader` is lazy, so corrupt files raised at `.pages`
  *outside* the error guard; broken-CMap PDFs yielding lone surrogates killed the write —
  now `errors="replace"`), and **stat-check** reads the nested
  `results[cfg]["metrics"]` schema some notebooks emit. 42 new offline tests (ref-audit,
  result-audit, panel_compile, pi-scout, plus both fixes' regressions, verified to fail on
  the pre-fix code) — the suite is now **162 tests**.

- **v1.8** — **New skill: pi-scout (14 total)** (`skills/pi-scout/`) — prospective-PI
  discovery for funded RA-ship/PhD outreach. Starts from the user's own papers
  (S2-verified seeds), harvests recent citing/recommended/searched papers, batch-fetches
  the author pool's metrics, and scores PI-likelihood with a transparent additive score
  that ranks *warm leads* (authors who already cite the user) first. The script stops
  where facts stop: current position, university tier (QS/THE + country), and funding
  signals are an agent-owned web-verification stage with mandatory source-URL + date
  provenance, feeding per-PI dossiers (trajectory, last-author share, group
  collaborators, connection-to-your-work evidence) and an entry-odds audit. Reuses the
  lit-review client like topic-watch, so it ships alongside it.

- **v1.7** — **Four new skills (13 total) + three upgrades**, targeting the remaining
  universal researcher pain points. New: **cite-check** (content-level citation
  verification — pairs every \cite-bearing claim sentence with the cited work's
  abstract from lit-review stores into a worksheet; the agent judges
  SUPPORTED/NOT-SUPPORTED/CANT-VERIFY quoting the source verbatim; catches
  miscitations existence checks miss); **data-audit** (dataset fingerprint +
  degeneracy detection — all-constant/all-null/duplicate columns, creeping
  nulls/NaNs — and drift verification with honest exit codes; the
  constant-column class of silent data bug is now mechanically catchable);
  **rebuttal** (reviewer comments → responses → `check` mechanically verifies
  every "we have revised…" claim against the actual revision diff via anchors +
  quotes; compiles RESPONSE.md); **submit-gate** (one-command audit battery →
  SUBMISSION_READINESS.md with a READY/NOT-READY verdict, plus `freeze`/
  `verify-freeze` sha256 snapshots of the submitted artifacts). Upgrades:
  **stat-check** gains TOST equivalence testing (`--tost <margin>`), 95% CIs on
  the mean paired difference, and Cohen's dz — with the new integrity rule that
  "no significant difference" is never upgraded to "equivalent/tied" without a
  TOST at a pre-declared margin; **bib-audit** gains a RETRACTED verdict
  (Retraction Watch data via OpenAlex `is_retracted`, most-severe rank);
  **claims-audit** now audits Markdown manuscripts too (frontmatter/code/
  citation-keys/URLs scrubbed — e.g. a JOSS paper.md). 31 new offline tests —
  the suite is now **120 tests** (119 at the v1.7 push; +1 regression test from the
  pre-submission diagnosis, which caught and fixed a v1.7 report bug: the stat-check
  markdown table separator was one cell short of the widened header, breaking table
  rendering on GitHub). The whole build was dogfooded through
  lab-notebook as a real 8-track investigation (including a live correction
  chain when a logged test count was wrong).
- **v1.6** — **New ninth skill: lab-notebook** (`skills/lab-notebook/`), an append-only
  cross-session log for investigations that outlive a single session and fan out into
  parallel tracks. Entries are typed (`progress`/`finding`/`blocker`/`decision`/
  `correction`/`next`), findings are nudged to cite evidence artifacts, corrections are
  new entries pointing at what they supersede (never edits), `status` prints a
  session-start digest (open blockers, queued next-steps with stale detection), and
  `compile` renders NOTEBOOK.md with **[superseded by #N]** markers so a corrected
  number can't be quoted unaware. Two sub-agent workflows keep the script as the truth
  layer: `audit` (a read-only sub-agent re-verifies every finding against its evidence
  artifact — field-tested) and `narrate` (a fresh-context sub-agent writes the
  investigation as one coherent story citing entry ids, mechanically enforced by the
  `check-narrative` subcommand: unknown ids fail, superseded citations warn, uncited
  findings are listed). Single-writer rule documented: sub-agents report, only the main
  session logs. 14 offline tests — the suite is now **88 tests**.
- **v1.5.1** — Two field-reported crash bugs in **lit-review**, both confirmed and fixed.
  (1) `snowball` crashed when Semantic Scholar answers `{"data": null}` — a literal null,
  not a missing key, so a `.get(k, [])` default never fires; the same latent pattern sat
  in `search`, `lookup`, `contexts`, and topic-watch's scan loop, and all five sites now
  use the `or []` idiom the bulk path already used. (2) `enrich`'s title fallback put raw
  titles into OpenAlex's `filter=title.search:` value, where `,` separates filters and
  `|` means OR with no escape mechanism (URL-encoding doesn't help — the API decodes
  before parsing), so a comma-bearing title drew an HTTP 400 that aborted the whole
  checkpointed run; titles are now sanitized (`_oa_filter_value`) and `openalex_get`
  treats 400 as a per-record miss rather than fatal. Six regression tests added — the
  offline suite is now **74 tests**. The arXiv paper's field-verification section
  records both.
- **v1.5** — **verify-run is now a real CLI** (`skills/verify-run/verify_run.py`), not a
  SKILL.md-only procedure: a stdlib-only, report-only integrity checklist over
  results.json files (config completeness, `smoke_test` flag, NaN/None metrics, futility
  stops with reason, exact-match parameter anchors, seed-count disclosure) that exits
  non-zero on any hard finding, so it doubles as a CI/pre-report gate. `templates/runner.py`
  was refactored for offline testability (papermill imported lazily inside `prepare_run`;
  the journal-summary/cast logic extracted to a pure `summarize_mape`), and both gained
  tests — the offline suite is now **68 tests**. The JOSS/arXiv papers gained a
  state-of-the-field comparison (Manubot [DOI-verified] and showyourwork position RIGOR as
  complementary — they govern manuscript *assembly*, RIGOR governs the agent-execution loop
  that produces the numbers) and an explicit target-audience statement; the JOSS paper was
  trimmed back to ≤1000 words; a stale claims-count in the arXiv abstract (39→47) was fixed;
  and the "eight skills paired with Python programs" phrasing was made precise (the
  remote-execution skill drives a shared runner template). CONTRIBUTING gained a
  "Getting help" section.
- **v1.4** — Bundled **worked examples** (`examples/` in the repo): fully-offline
  synthetic demos for stat-check (a significant win vs. a seed-noise "win" at n=8) and
  claims-audit (seeded MATCHED / NEAR-MISS / ORPHAN claims), plus a network bib-audit
  demo that catches a deliberately fabricated citation — the offline demos run in CI
  (`tests/test_examples.py`), so the walkthroughs cannot drift from the code. Papers
  gained measured runtimes from the first install (14-entry bib audit in 51 s; 271
  manuscript claims classified in 0.25 s) and softened absolute phrasing ("failures
  surface mechanically" rather than "cannot happen"). 50 offline tests.
- **v1.3** — **colab-run field-verified** on a live Colab VM (token-stamped round trip
  through the synced folder); the field test caught and fixed a boolean-injection bug
  (`json.loads` turning `False` into the truthy string `'False'` — smoke mode could
  never be disabled), now a regression test. Backend guidance made explicit: Kaggle =
  recommended fully-headless default, Colab = deliberate semi-attended fallback.
  **lit-review `report` reworked**: rich per-paper entries (title, S2/DOI/PDF links,
  provenance, best-available abstract with source attribution) and `--focus` /
  `--focus-file` relevance ranking — the collection tiers into Core/Related/Peripheral
  against the researcher's own project description via a transparent IDF-weighted
  term-overlap score with matched terms shown per paper. 47 offline tests.
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

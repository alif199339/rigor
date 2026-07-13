# RIGOR — Research Integrity Guardrails for Open Research

[![tests](https://github.com/alif199339/rigor/actions/workflows/ci.yml/badge.svg)](https://github.com/alif199339/rigor/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**An integrity-first agent for the full research workflow — grounded literature,
verified experiments, honest statistics, auditable manuscripts.** RIGOR turns an LLM
coding agent (built for [Claude Code](https://claude.com/claude-code), adaptable to any
harness that reads skill folders) into a research assistant that surveys the field,
runs your GPU experiments unattended on free cloud compute, tests your claims
statistically, and keeps your manuscript honest — with every capability *grounded by
construction*, so the three classic failure modes of AI-assisted research can't happen:

| Failure mode | RIGOR's guardrail |
|---|---|
| **Hallucinated citations** — the model "remembers" a paper that doesn't exist | A paper may be cited **only** if the live Semantic Scholar API returned it into a provenance-tagged store (`papers.json`). Remembered papers must be verified by `lookup` first. |
| **Stale/fabricated numbers** — the manuscript says 3.56 but the regenerated table says 3.48 | `claims-audit` reconciles every numeric claim in the prose against the machine-generated tables + raw results; `bib-audit` does the same for every bibliography entry. Both **report and propose — they never auto-edit**. |
| **Seed-noise claims** — "model X beats Y" on a gap that's inside random variation | `stat-check` runs paired-by-seed Wilcoxon/t tests and reports exact p-values and n. A non-significant result is a result, never an omission. |

## The nine skills

| Skill | What it does |
|---|---|
| [`lit-review`](skills/lit-review/SKILL.md) | Grounded literature discovery: `search` (ranked or `--bulk` boolean coverage), `lookup`, `snowball`, `recommend`, citation `contexts` (the sentences citing a paper + intent tags), OpenAlex `enrich` (abstract holes, provenance-tagged), `refresh` (batch citation-count updates with as-of dates), `pdfs` (validated open-access downloads), `fulltext` (page-tagged text so body claims cite pages). `report` renders every paper as a rich entry — title, S2/DOI/PDF links, provenance, best-available abstract — and with `--focus "your project"` **re-ranks and tiers the whole collection (Core/Related/Peripheral) by relevance to *your* work**, with the matched terms shown so the ranking is inspectable, not a black box. |
| [`bib-audit`](skills/bib-audit/SKILL.md) | Verifies every `.bib` entry against Semantic Scholar + Crossref: wrong years, drifted titles, missing DOIs, unresolvable works. Preprint-vs-publication year offsets are recognized, not false-flagged. |
| [`claims-audit`](skills/claims-audit/SKILL.md) | Extracts every numeric literal from a LaTeX manuscript's prose/captions and classifies it against ground truth (generated tables + results JSON): MATCHED / NEAR-MISS (stale drift) / ORPHAN. Also flags results-derived figures older than the newest results file. |
| [`stat-check`](skills/stat-check/SKILL.md) | Paired-by-seed Wilcoxon signed-rank + paired t across multi-seed runs, with optional Holm correction. Groups runs exactly like your aggregator (newest run per seed supersedes; smoke runs excluded). |
| [`topic-watch`](skills/topic-watch/SKILL.md) | Re-runs a collection's own recorded queries and diffs for papers published since — keeps a survey current before a revision. Manual by design. |
| [`run-remote`](skills/run-remote/SKILL.md) | Drives unattended notebook execution on Kaggle's free GPU (papermill parameter injection → push → poll → download → parse), with quota safety: GPU is never enabled without the owner's explicit yes. |
| [`colab-run`](skills/colab-run/SKILL.md) | Google Colab as a **fallback** free-GPU backend, honestly: free Colab has no headless-execution API, so the agent injects parameters and stages the notebook into your Drive-synced folder, you tap "Run all" once (that tap **is** the GPU approval), and the agent polls the synced folder, validates `results.json`, and journals it. **Field-verified end-to-end.** For full automation (overnight fleets, zero taps), use `run-remote`/Kaggle — that's the recommended default. |
| [`verify-run`](skills/verify-run/SKILL.md) | The integrity checklist every completed run passes before its numbers reach a human: config completeness, smoke-test flags, futility stops, NaNs, parameter-count fingerprints, seed-count disclosure. |
| [`lab-notebook`](skills/lab-notebook/SKILL.md) | Append-only, cross-session lab notebook for investigations that outlive a single session and fan out into parallel tracks. Grounded `progress`/`finding`/`blocker`/`decision` entries per track (findings cite evidence artifacts; corrections are new entries, never edits), a session-start `status` digest, and a compiled `NOTEBOOK.md` that marks superseded entries. Sub-agent workflows re-verify findings against their evidence (`audit`) and write the investigation as one coherent story whose every claim cites entry ids — mechanically enforced by `check-narrative`. |

The division of labor is deliberate: **scripts do the mechanical extraction; the agent
does the semantic adjudication** (per each skill's `SKILL.md` rules); **the human approves
every edit**. Nothing in RIGOR rewrites your manuscript, bibliography, or results.

## Install

RIGOR is a copy-paste folder — no package manager, no build step.

```bash
git clone https://github.com/alif199339/rigor
cp -r rigor/skills <your-project>/.claude/skills   # merge if .claude/skills exists
```

(or download the archive attached to the
[latest release](https://github.com/alif199339/rigor/releases/latest))

Then open a fresh Claude Code session in your project and say:

> Read `.claude/skills/RESEARCH_AGENT.md` and onboard this project.

The agent scans your repo, asks only what it can't infer, and writes the per-project
config (`skills/_shared/project_profile.yaml`). See
[`skills/RESEARCH_AGENT.md`](skills/RESEARCH_AGENT.md) for the manifest, machine setup
(API keys, interpreters), and the no-clash guarantees for host projects.

### One-time machine setup

```powershell
setx S2_API_KEY "<key>"                    # free: semanticscholar.org/product/api (1 req/s)
python -m pip install pypdf scipy          # fulltext / stat-check
python -m pip install kaggle papermill pyyaml   # only if you use run-remote
```

The scripts are stdlib-only Python 3.10+ except where noted. Windows: set `PYTHONUTF8=1`.

## Try it in five minutes — bundled examples (offline, no API key)

The repo ships synthetic worked examples in [`examples/`](examples/README.md) so you can
exercise the audit and statistics tools before wiring up anything real — including,
fully offline: a multi-seed study where `stat-check` shows one model comparison is
genuinely significant and another is seed noise, and a tiny manuscript where
`claims-audit` flags a seeded stale number and an orphan. A third (network) demo has
`bib-audit` catch a deliberately **fabricated citation**. The offline examples run in CI,
so the walkthroughs can't drift from the code:

```bash
python skills/stat-check/stat_check.py \
    --runs-glob "examples/stat-check-demo/runs/*/output/results.json" \
    --studies examples/stat-check-demo/studies.json \
    --study demo --pairs small_A:small_B,small_A:big_baseline --holm
```

Expected output and the other two demos: [`examples/README.md`](examples/README.md).

## Quickstart (scripts also work standalone, without any agent)

```bash
# build a grounded literature collection, then rank it by relevance to YOUR project
python skills/lit-review/lit_search.py --out literature/my-topic search --query "your topic" --limit 25
python skills/lit-review/lit_search.py --out literature/my-topic report \
       --focus "one paragraph describing your project or question"   # tiers: Core/Related/Peripheral

# audit a bibliography (report-only)
python skills/bib-audit/bib_audit.py --bib references.bib --mailto you@example.com

# reconcile manuscript numbers vs generated tables + results
python skills/claims-audit/claims_audit.py --tex main.tex --tables tables \
       --results "sweeps/runs/*/output/results.json"

# is that "X beats Y" claim real?
python skills/stat-check/stat_check.py --runs-glob "sweeps/runs/*/output/results.json" --list
python skills/stat-check/stat_check.py --runs-glob "..." --study mystudy --pairs modelA:baseline --holm

# integrity-check a completed run before trusting its numbers (exits non-zero on any hard finding)
python skills/verify-run/verify_run.py --runs-glob "sweeps/runs/*/output/results.json" \
       --expect modelA,baseline --anchors modelA=45393
```

Every command's options and integrity rules are documented in its skill's `SKILL.md`.

## Results format (stat-check / claims-audit / verify-run)

One JSON file per run:

```json
{
  "notebook": "my_experiment.ipynb",
  "seed": 42,
  "smoke_test": false,
  "span_start": null,
  "timestamp_utc": "2026-07-04T20:11:26+00:00",
  "results": {
    "modelA":   {"rmse": 651.0, "mae": 464.3, "mape": 3.56, "params": 45393, "futility_stop": null},
    "baseline": {"rmse": 812.9, "mae": 590.1, "mape": 4.22, "params": 121729, "futility_stop": null}
  }
}
```

Runs group into named studies via an optional
[`skills/_shared/studies.json`](skills/_shared/studies.example.json) map; without it,
studies are auto-named from the notebook + span.

## Tests

```bash
python -m pip install pytest scipy pypdf pyyaml
pytest -q
```

The suite is fully offline — all HTTP is stubbed — and CI runs it on Linux + Windows,
Python 3.10 and 3.12.

## Feedback & adoption

Using RIGOR — or tried it and hit friction? Open a
[Discussion](https://github.com/alif199339/rigor/discussions) for adoption reports,
questions, and ideas, or an [Issue](https://github.com/alif199339/rigor/issues) for
bugs. Where and how RIGOR is used directly shapes the roadmap — and an adoption
report is the one signal an open-source maintainer cannot measure any other way.

## Citation & attribution

If RIGOR contributes to your research, please cite it (see [`CITATION.cff`](CITATION.cff)
— GitHub's "Cite this repository" button works). Published material built on collections
from the `lit-review` skill should also cite **Kinney et al., *The Semantic Scholar Open
Data Platform* (2023, DOI 10.48550/arXiv.2301.10140)** per the Semantic Scholar API
license, and OpenAlex-sourced abstracts are tagged and should be attributed.

## License

[MIT](LICENSE) © 2026 Alif Mahmud

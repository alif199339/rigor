# RIGOR — Research Integrity Guardrails for Open Research

[![tests](https://github.com/alif199339/rigor/actions/workflows/ci.yml/badge.svg)](https://github.com/alif199339/rigor/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**An integrity-first agent toolkit for AI-assisted research.** RIGOR gives an LLM coding
agent (built for [Claude Code](https://claude.com/claude-code), adaptable to any harness
that reads skill folders) a set of research capabilities that are *grounded by
construction* — so the three classic failure modes of AI-assisted research can't happen:

| Failure mode | RIGOR's guardrail |
|---|---|
| **Hallucinated citations** — the model "remembers" a paper that doesn't exist | A paper may be cited **only** if the live Semantic Scholar API returned it into a provenance-tagged store (`papers.json`). Remembered papers must be verified by `lookup` first. |
| **Stale/fabricated numbers** — the manuscript says 3.56 but the regenerated table says 3.48 | `claims-audit` reconciles every numeric claim in the prose against the machine-generated tables + raw results; `bib-audit` does the same for every bibliography entry. Both **report and propose — they never auto-edit**. |
| **Seed-noise claims** — "model X beats Y" on a gap that's inside random variation | `stat-check` runs paired-by-seed Wilcoxon/t tests and reports exact p-values and n. A non-significant result is a result, never an omission. |

## The eight skills

| Skill | What it does |
|---|---|
| [`lit-review`](skills/lit-review/SKILL.md) | Grounded literature discovery: `search` (ranked or `--bulk` boolean coverage), `lookup`, `snowball`, `recommend`, citation `contexts` (the sentences citing a paper + intent tags), OpenAlex `enrich` (abstract holes, provenance-tagged), `refresh` (batch citation-count updates with as-of dates), `report` (ranked index + BibTeX), `pdfs` (validated open-access downloads), `fulltext` (page-tagged text so body claims cite pages). |
| [`bib-audit`](skills/bib-audit/SKILL.md) | Verifies every `.bib` entry against Semantic Scholar + Crossref: wrong years, drifted titles, missing DOIs, unresolvable works. Preprint-vs-publication year offsets are recognized, not false-flagged. |
| [`claims-audit`](skills/claims-audit/SKILL.md) | Extracts every numeric literal from a LaTeX manuscript's prose/captions and classifies it against ground truth (generated tables + results JSON): MATCHED / NEAR-MISS (stale drift) / ORPHAN. Also flags results-derived figures older than the newest results file. |
| [`stat-check`](skills/stat-check/SKILL.md) | Paired-by-seed Wilcoxon signed-rank + paired t across multi-seed runs, with optional Holm correction. Groups runs exactly like your aggregator (newest run per seed supersedes; smoke runs excluded). |
| [`topic-watch`](skills/topic-watch/SKILL.md) | Re-runs a collection's own recorded queries and diffs for papers published since — keeps a survey current before a revision. Manual by design. |
| [`run-remote`](skills/run-remote/SKILL.md) | Drives unattended notebook execution on Kaggle's free GPU (papermill parameter injection → push → poll → download → parse), with quota safety: GPU is never enabled without the owner's explicit yes. |
| [`colab-run`](skills/colab-run/SKILL.md) | Google Colab as a second free-GPU backend, honestly: free Colab has no headless-execution API, so the agent injects parameters and stages the notebook into your Drive-synced folder, you tap "Run all" once (that tap **is** the GPU approval), and the agent polls the synced folder, validates `results.json`, and journals it. |
| [`verify-run`](skills/verify-run/SKILL.md) | The integrity checklist every completed run passes before its numbers reach a human: config completeness, smoke-test flags, futility stops, NaNs, parameter-count fingerprints, seed-count disclosure. |

The division of labor is deliberate: **scripts do the mechanical extraction; the agent
does the semantic adjudication** (per each skill's `SKILL.md` rules); **the human approves
every edit**. Nothing in RIGOR rewrites your manuscript, bibliography, or results.

## Install

RIGOR is a copy-paste folder — no package manager, no build step.

```bash
git clone https://github.com/alif199339/rigor
cp -r rigor/skills <your-project>/.claude/skills   # merge if .claude/skills exists
```

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

## Quickstart (scripts also work standalone, without any agent)

```bash
# build a grounded literature collection
python skills/lit-review/lit_search.py --out literature/my-topic search --query "your topic" --limit 25
python skills/lit-review/lit_search.py --out literature/my-topic report

# audit a bibliography (report-only)
python skills/bib-audit/bib_audit.py --bib references.bib --mailto you@example.com

# reconcile manuscript numbers vs generated tables + results
python skills/claims-audit/claims_audit.py --tex main.tex --tables tables \
       --results "sweeps/runs/*/output/results.json"

# is that "X beats Y" claim real?
python skills/stat-check/stat_check.py --runs-glob "sweeps/runs/*/output/results.json" --list
python skills/stat-check/stat_check.py --runs-glob "..." --study mystudy --pairs modelA:baseline --holm
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
python -m pip install pytest scipy pypdf
pytest -q
```

The suite is fully offline — all HTTP is stubbed — and CI runs it on Linux + Windows,
Python 3.10 and 3.12.

## Citation & attribution

If RIGOR contributes to your research, please cite it (see [`CITATION.cff`](CITATION.cff)
— GitHub's "Cite this repository" button works). Published material built on collections
from the `lit-review` skill should also cite **Kinney et al., *The Semantic Scholar Open
Data Platform* (2023, DOI 10.48550/arXiv.2301.10140)** per the Semantic Scholar API
license, and OpenAlex-sourced abstracts are tagged and should be attributed.

## License

[MIT](LICENSE) © 2026 Alif Mahmud

---
title: 'RIGOR: Research Integrity Guardrails for Open Research — an integrity-first agent toolkit for AI-assisted science'
tags:
  - research integrity
  - AI agents
  - large language models
  - literature review
  - citation verification
  - reproducibility
  - statistical testing
  - Python
authors:
  - name: Alif Mahmud
    orcid: 0009-0001-5756-2202
    affiliation: 1
affiliations:
  - name: Department of Electrical and Electronic Engineering, Bangladesh University of Engineering and Technology (BUET), Dhaka, Bangladesh
    index: 1
date: 12 July 2026
bibliography: paper.bib
---

# Summary

Large language model (LLM) agents increasingly work across the research
workflow — surveying literature, running experiments, drafting manuscripts.
Used naively, they import three well-documented failure modes:
**hallucinated citations** (models fabricate plausible references at high rates
[@walters2023fabrication]), **stale or unsupported numbers** (prose that no
longer matches regenerated results tables), and **seed-noise claims** ("model X
beats model Y" on a gap smaller than run-to-run variation).

RIGOR is a toolkit of nine *skills* — each an instruction file paired with a
small, dependency-light Python program (the remote-execution skill drives a
shared runner template) — that make an LLM agent's research assistance
*grounded by construction* rather than by exhortation. Each skill combines
mechanical extraction with explicit integrity rules the agent must follow and a
human approval gate for every proposed change:

- **lit-review** builds literature collections exclusively from live Semantic
  Scholar API responses [@kinney2023semanticscholar]; a paper may be cited only
  if it exists in the resulting provenance-tagged store. It spans ranked and
  exhaustive search, snowballing, recommendations, citation-context and
  page-tagged full-text retrieval [@pypdf], and separately-attributed OpenAlex
  abstract enrichment [@priem2022openalex]. Its `report` command tiers a
  collection (Core/Related/Peripheral) against the researcher's own project
  description by an inspectable relevance score.
- **bib-audit** verifies every BibTeX entry against Semantic Scholar and
  Crossref [@hendricks2020crossref], classifying entries as verified,
  mismatched (with field-level diffs), not found, non-paper resources (checked
  by URL liveness), or unverifiable. It recognizes preprint-vs-publication
  year offsets and never auto-suggests fixes across likely different editions.
- **claims-audit** extracts every numeric literal from a LaTeX manuscript's
  prose and captions and reconciles it against machine-generated tables and raw
  results files, flagging near-misses (stale drift), orphans (numbers with no
  data source), unfilled placeholders, and stale results-derived figures.
- **stat-check** runs paired-by-seed Wilcoxon signed-rank
  [@wilcoxon1945individual] and paired *t* tests [@virtanen2020scipy] over
  multi-seed results, with optional Holm correction [@holm1979simple]; it always
  reports exact *p*-values and *n*, and a non-significant result is a result,
  never an omission.
- **topic-watch** re-runs a collection's own recorded queries and diffs for
  newly published papers.
- **lab-notebook** keeps an append-only, cross-session log of long multi-track
  investigations: findings cite the artifacts that show them, corrections
  supersede rather than edit, and agent-written narrative compilations are
  mechanically checked (`check-narrative`) so every claim cites a real,
  un-superseded log entry.
- **run-remote**, **colab-run**, and **verify-run** dispatch parameterized
  notebooks [@papermill] to free cloud GPUs (Kaggle headless, Colab
  semi-attended and field-verified) and pass every completed run through an
  integrity checklist — configuration completeness, smoke-test flags,
  divergence aborts, parameter-count fingerprints, seed-count disclosure —
  before any number reaches the researcher.

The scripts are standard-library-first Python (3.10+; scipy and pypdf only
where noted) and usable standalone. The skill layer targets agent harnesses
that read skill folders, such as Claude Code [@claudecode]; an offline test
suite (all HTTP stubbed) runs in CI on Linux and Windows.

# Statement of need

Surveys of LLM-generated bibliographies find large fractions of fabricated or
substantively erroneous citations [@walters2023fabrication]. Commercial
AI-assisted discovery tools address parts of this problem but are typically
closed, non-scriptable, and unauditable, and do not cover the two further
integrity gaps of agent-run experiments: numbers drifting between regenerated
results and prose, and statistically unsupported superiority claims.

Open, scriptable tools already exist for adjacent problems. Manubot assembles
manuscripts from version-controlled sources and resolves citations by persistent
identifier [@himmelstein2019manubot], and showyourwork binds an article's
figures to the code that regenerates them [@showyourwork]; both target
reproducible *manuscript assembly*. RIGOR is complementary: it governs the
agent-execution loop that produces the numbers and citations in the first
place — grounding literature retrieval in a live API, gating superiority claims
on paired-by-seed tests, and auditing (never editing) the manuscript against its
own data. Its target audience is researchers using LLM agents anywhere in an
empirical workflow, particularly small, compute-constrained groups running
multi-seed studies on free cloud GPUs.

RIGOR's contribution is architectural rather than algorithmic: citations must
originate from a live bibliographic API response stored with provenance;
manuscripts are diffed against their own ground-truth data; superiority claims
are gated on paired tests. The **division of
labor** is explicit: scripts perform mechanical extraction; the agent performs
semantic adjudication under written rules; the human approves every edit. Audit
tools *report and propose — they never modify* the bibliography or manuscript.

A two-layer design makes the toolkit portable: skills and scripts carry no
project paths or reference numbers; a per-project profile carries paths,
globs, interpreters, and safety flags. Installation is a folder copy;
credentials never travel with it.

# Quality control and a working case study

Beyond the CI suite, RIGOR is dogfooded on a real research project (a
multi-seed regional electricity-load forecasting study) as its first install,
where each tool produced verifiable findings:

- *bib-audit* audited the study's 14-entry bibliography in 51 seconds, catching
  one reference whose title paraphrased the real paper's, flagging three
  title-only matches to different editions (no auto-fix), verifying two dataset
  URLs, and recovering five missing DOIs.
- *claims-audit* classified the manuscript's 271 numeric claims (151 matched,
  47 near-miss, 73 orphan) against its generated tables and raw results in 0.25
  seconds — mechanizing a manual audit that had found nine real text-vs-data
  mismatches.
- *stat-check*, over a 14-model, 10-seed study, showed the nominally best model
  was statistically indistinguishable from several siblings (Wilcoxon
  $p \approx 0.5$) while every family-versus-baseline claim survived
  ($p = 0.002$–$0.02$) — converting a single-seed "best model" headline into a
  defensible one.
- The bibliography of this paper was itself verified by resolving every DOI
  through doi.org content negotiation before inclusion.

Nothing in the scripts is specific to this domain. To make that reviewable
without any API key, GPU, or agent harness, the repository ships fully offline,
synthetic worked examples (`examples/`) for the statistics and audit tools, plus
a network demo in which *bib-audit* catches a deliberately fabricated citation.
The offline examples are exercised end-to-end by the CI suite, so the documented
walkthroughs cannot drift from the code.

# Acknowledgements

RIGOR builds on the public APIs of Semantic Scholar
[@kinney2023semanticscholar], OpenAlex [@priem2022openalex], and Crossref
[@hendricks2020crossref], respecting their rate limits and attribution
requirements by design. No external funding supported this work.

# References

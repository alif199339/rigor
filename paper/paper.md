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
date: 11 July 2026
bibliography: paper.bib
---

# Summary

Large language model (LLM) agents are increasingly used across the research
workflow — surveying literature, running experiments, and drafting manuscripts.
Used naively, they import three well-documented failure modes into science:
**hallucinated citations** (models fabricate plausible references at high rates
[@walters2023fabrication]), **stale or unsupported numbers** (prose that no
longer matches regenerated results tables), and **seed-noise claims** ("model X
beats model Y" on a gap smaller than run-to-run random variation).

RIGOR is a toolkit of seven *skills* — paired instruction files and small,
dependency-light Python programs — that make an LLM agent's research assistance
*grounded by construction* rather than by exhortation. Each skill combines a
mechanical extraction script with explicit integrity rules the agent must
follow, and a human approval gate for every proposed change:

- **lit-review** builds literature collections exclusively from live Semantic
  Scholar API responses [@kinney2023semanticscholar]; a paper may be cited only
  if it exists in the resulting provenance-tagged store. Sub-commands cover
  ranked and exhaustive (boolean) search, citation snowballing,
  recommendations, citation-context retrieval (the sentences in which a paper
  is cited, with intent tags), abstract enrichment from OpenAlex
  [@priem2022openalex] into a separately-attributed field, batch refresh of
  citation counts with as-of dates, validated open-access PDF retrieval, and
  page-tagged full-text extraction [@pypdf] so body-level claims cite pages.
- **bib-audit** verifies every BibTeX entry against Semantic Scholar and
  Crossref [@hendricks2020crossref], classifying entries as verified,
  mismatched (with field-level diffs), not found, non-paper resources (checked
  by URL liveness), or unverifiable. It recognizes preprint-vs-publication
  year offsets and refuses to auto-suggest "fixes" when a title-only match is
  likely a different edition of the work.
- **claims-audit** extracts every numeric literal from a LaTeX manuscript's
  prose and captions and reconciles it against machine-generated tables and raw
  results files, flagging near-misses (stale drift), orphans (numbers traceable
  to no data source), unfilled placeholders, and results-derived figures older
  than the newest results file.
- **stat-check** runs paired-by-seed Wilcoxon signed-rank
  [@wilcoxon1945individual] and paired *t* tests [@virtanen2020scipy] over
  multi-seed experiment results, with optional Holm correction [@holm1979simple],
  always reporting exact *p*-values and *n* — a non-significant result is a
  result, never an omission.
- **topic-watch** re-runs a collection's own recorded queries and diffs for
  newly published papers.
- **run-remote** and **verify-run** dispatch parameterized notebooks
  [@papermill] to free cloud GPUs unattended and pass every completed run
  through an integrity checklist (configuration completeness, smoke-test flags,
  divergence aborts, parameter-count fingerprints, seed-count disclosure)
  before any number reaches the researcher.

The scripts are standard-library-first Python (3.10+; scipy and pypdf only
where noted), fully usable standalone from the command line. The skill layer
targets agent harnesses that read skill folders, such as Claude Code
[@claudecode]. An offline test suite (all HTTP stubbed) runs in CI on Linux and
Windows.

# Statement of need

Surveys of LLM-generated bibliographies find large fractions of fabricated or
substantively erroneous citations [@walters2023fabrication]. Commercial
AI-assisted discovery tools address parts of this problem but are typically
closed, non-scriptable, and unauditable, and they do not extend to the other
two integrity gaps that arise once agents run experiments and edit manuscripts:
numbers drifting between regenerated results and prose, and statistically
unsupported superiority claims.

RIGOR's contribution is architectural rather than algorithmic. It reframes each
integrity problem so the failure cannot happen silently: citations must
originate from a live bibliographic API response stored with provenance;
manuscripts are diffed against their own ground-truth data; superiority claims
are gated on paired tests of the actual per-seed results. The **division of
labor** is explicit: scripts perform mechanical extraction; the agent performs
semantic adjudication under written rules; the human approves every edit. Audit
tools *report and propose — they never modify* the bibliography or manuscript.

A two-layer design makes the toolkit portable across projects: the skills and
scripts contain no project paths, names, or reference numbers (Layer 1), while
a per-project profile file carries manuscript paths, results globs,
interpreters, and safety flags (Layer 2). Installation is a folder copy, and
credentials never travel with it.

# Quality control and a working case study

Beyond the CI suite, RIGOR is dogfooded on a real research project (a
multi-seed regional electricity-load forecasting study) as its first install,
where each tool produced verifiable findings:

- *bib-audit*, on a 14-entry manuscript bibliography, caught one reference whose
  title was a paraphrase of the real (existing) paper's title, flagged three
  title-only matches to different editions or records (deliberately proposing
  no auto-fix), verified two dataset resources by URL, and recovered five
  missing DOIs.
- *claims-audit*, on the accompanying manuscript, classified 266 numeric claims
  (154 matched, 39 near-miss, 73 orphan) and flagged 12 results-derived figures
  as stale relative to the newest experiment results — mechanizing an earlier
  manual audit that had found nine real text-vs-data mismatches.
- *stat-check*, over a 14-model, 10-seed study, showed that the nominally best
  model was statistically indistinguishable from several siblings (Wilcoxon
  $p \approx 0.5$) while the family-versus-baseline claims survived exactly
  ($p = 0.002$–$0.02$) — converting a single-seed "best model" headline into an
  honest, defensible claim.
- The bibliography of this paper was itself verified by resolving every DOI
  through doi.org content negotiation before inclusion.

# Acknowledgements

RIGOR builds on the public APIs of Semantic Scholar
[@kinney2023semanticscholar], OpenAlex [@priem2022openalex], and Crossref
[@hendricks2020crossref], and respects their rate limits and attribution
requirements by design. No external funding supported this work.

# References

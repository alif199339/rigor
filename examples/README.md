# Worked examples — try RIGOR in five minutes

Self-contained demos of the three audit/statistics tools on **synthetic data that ships
with the repo**. The first two are **fully offline** (no API key, no agent harness, no
GPU — just Python), and both are exercised by the CI test suite
(`tests/test_examples.py`), so they can never silently rot. All commands are run from
the repository root.

## 1. stat-check — is "model X beats model Y" real, or seed noise? *(offline)*

`stat-check-demo/runs/` holds eight seeds of a synthetic three-model study, crafted so
that one comparison is genuinely significant and one is pure noise:

```bash
python skills/stat-check/stat_check.py \
    --runs-glob "examples/stat-check-demo/runs/*/output/results.json" \
    --studies examples/stat-check-demo/studies.json \
    --study demo --pairs small_A:small_B,small_A:big_baseline --holm
```

Expected verdicts (needs `scipy`):

| Pair | n | Wilcoxon p | verdict |
|---|---|---|---|
| small_A vs small_B | 8 | 0.8203 | no significant difference |
| small_A vs big_baseline | 8 | 0.0078 | **small_A wins** (Holm-adj) |

The point: `small_A` and `small_B` differ on every individual seed — a single-seed run
would happily declare one of them "the best model" — but the paired test shows the
ranking is noise. Against `big_baseline` the win holds on all 8 seeds
(p = 2/2⁸ = 0.0078, the two-sided floor at n=8, honestly disclosed as such).

## 2. claims-audit — does the manuscript still match its own data? *(offline)*

`claims-audit-demo/` is a four-sentence "manuscript" (`paper.tex`) plus its
machine-generated table, seeded with one claim of each kind:

```bash
python skills/claims-audit/claims_audit.py \
    --tex examples/claims-audit-demo/paper.tex \
    --tables examples/claims-audit-demo/tables \
    --results "examples/stat-check-demo/runs/*/output/results.json" \
    --studies examples/stat-check-demo/studies.json \
    --out examples/claims-audit-demo/claims_audit_report.md
```

Expected: `4 claims: 2 matched, 1 near-miss, 1 orphan` —

- `10.30` and `18,442` **MATCHED** (agree with the generated table),
- `11.21` **NEAR-MISS** (the table says 11.15 — the classic "table re-swept, prose not
  updated" stale drift),
- `47.5` **ORPHAN** (appears in no table or results file — a human must adjudicate
  whether it is a legitimate derived value or unsupported).

The report is written next to the manuscript; the manuscript itself is never edited.

## 3. bib-audit — catch the hallucinated citation *(needs network)*

`bib-audit-demo/demo.bib` has three entries: one correct, one real paper with the wrong
year, and one **fabricated** reference of the kind LLMs invent:

```bash
python skills/bib-audit/bib_audit.py \
    --bib examples/bib-audit-demo/demo.bib --mailto you@example.com \
    --out examples/bib-audit-demo/bib_audit_report.md
```

Expected (verified 2026-07-12; takes well under a minute with an `S2_API_KEY`, a few
minutes keyless due to polite rate-limit backoff):

| Entry | Verdict |
|---|---|
| `lecun2015deep` | VERIFIED |
| `vaswani2014attention` | **MISMATCH** — year: `2014` → `2017` |
| `doe2023quantum` | **NOT-FOUND** — no confident title match in S2 or Crossref |

Report-only, like every RIGOR audit: it proposes the year fix and flags the fabrication;
you decide.

## Notes

- The demo data is synthetic and domain-neutral — model names, metrics, and values are
  invented for pedagogy and correspond to no real study.
- The same three commands work unchanged on your own project: point `--runs-glob` at
  your runs, `--tex/--tables` at your manuscript, `--bib` at your bibliography. Study
  naming for your own runs is covered in `skills/_shared/studies.example.json`.
- The remaining skills need live external services (Semantic Scholar for `lit-review` /
  `topic-watch`, a Kaggle account for `run-remote`, Google Drive for Desktop for
  `colab-run`) — their `SKILL.md` files each contain a copy-paste quickstart.

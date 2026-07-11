---
name: lit-review
description: Build a grounded literature review from the Semantic Scholar API (200M+ papers) -- real, verified DOIs/titles/PDFs only, never model-memory citations. Takes a plain-English topic or an existing manuscript, pulls relevant papers into a dedicated literature/<slug>/ folder (papers.json/md/bib + open-access PDFs), and synthesizes a review with a gaps-and-ideas section for brainstorming.
---

# /lit-review — grounded literature discovery, zero hallucinated citations

Turns a plain-English research question ("what's been done on X?") **or** an existing
manuscript into a verified paper collection + synthesized review. The tool is
`.claude/skills/lit-review/lit_search.py` (stdlib-only, any Python 3.10+; use the
project's smoke venv or `py -3.11`). All API calls hit the live Semantic Scholar
Academic Graph, so every title/DOI/URL/PDF in the output exists by construction.

## The non-negotiable integrity rules (the reason this skill exists)

1. **A paper may be cited/reported ONLY if it appears in `papers.json`** (i.e., was
   returned by the API this session). Never add a reference from model memory.
2. If you *remember* a relevant paper, you must first **verify it exists**:
   `lookup --title "..."` (or `--id DOI:...`). If the API can't find it, it does not
   go in the review — say so instead.
3. Every claim you write **about** a paper must be traceable to its `abstract`/`tldr`
   fields in `papers.json`. If a paper has no abstract, describe it only by
   title/venue/year and mark it "(abstract unavailable — verify before citing claims)".
4. Report citation counts and years exactly as the API returned them; never estimate.
5. A claim grounded in a paper's **body** (via `fulltext`, not just its abstract) must
   cite the page tag, e.g. "(Su 2024, p.4)", so it stays checkable. Abstract-level claims
   stay as in rule 3.

## Inputs (either)

- **Plain-English topic** — e.g. "transformer models for day-ahead electricity price
  forecasting". Derive 3–6 diverse search queries: canonical phrasing, method-centric,
  application-centric, and a survey-seeking one ("... survey" / "... review").
- **Existing manuscript** — read the paper (`main.tex` / draft / thesis chapter) and its
  `.bib`. Extract (a) 3–6 topic queries from its title/abstract/keywords, and
  (b) 2–4 **seed papers** (its most central existing references, by DOI/arXiv id)
  for snowballing and recommendations.

## Workflow

Pick a kebab-case slug for the topic; everything lands in `<literature_dir>/<slug>/`
(`literature_dir` from `_shared/project_profile.yaml`; default `literature/`). `PY` below
= any Python 3.10+ — use the profile's `python_pypdf` (needs pypdf for the `fulltext`
step).

```powershell
$env:PYTHONUTF8="1"
$T = ".claude/skills/lit-review/lit_search.py"
$OUT = "literature/<slug>"

# 1. breadth: one search per derived query (25 each is plenty; --year-from optional)
PY $T --out $OUT search --query "<query 1>" --limit 25
PY $T --out $OUT search --query "<query 2>" --limit 25 --year-from 2018
# ... queries 3-6

# 2. depth: snowball + recommendations from the 2-4 most central papers found
#    (seed = S2 paperId from papers.json, or DOI:10..../ARXIV:2101.00001)
PY $T --out $OUT snowball  --seed DOI:10.24963/ijcai.2018/505 --direction both --limit 50
PY $T --out $OUT recommend --seed DOI:10.24963/ijcai.2018/505 --limit 20

# 3. verify any remembered papers before they may be mentioned at all
PY $T --out $OUT lookup --title "Attention is all you need"

# 3b. optional: fill '(abstract unavailable)' holes from OpenAlex (own field, attributed)
PY $T --out $OUT enrich --source openalex

# 4. regenerate the human-readable index + BibTeX from the deduped store
PY $T --out $OUT report

# 5. optional: pull open-access PDFs of the top-ranked papers (arXiv fallback built in)
PY $T --out $OUT pdfs --top 8

# 6. optional: extract page-tagged full text from those PDFs to ground body-level claims
#    (needs pypdf -- run this step with the profile's python_pypdf interpreter)
py -3.11 $T --out $OUT fulltext --all        # or --top 8; --force re-extracts
```

Folder after a run:

```
literature/<slug>/
├── papers.json   # raw verified metadata, deduped by S2 paperId, provenance-tagged
├── papers.md     # ranked index table + recent-work digest (regenerate via `report`)
├── papers.bib    # BibTeX (S2's own citationStyles entries), \bibliography-ready
├── pdfs/         # open-access PDFs, `<year>_<title>.pdf`
├── fulltext/     # page-tagged plain text (`===== PDF PAGE N =====`) from `fulltext`
└── contexts/     # `<seed>.md` — citing sentences + intent tags, from `contexts`
```

## Two more discovery modes

- **`contexts --seed <id>`** — how the field actually cites a paper. Pulls the
  `/citations` endpoint's `contexts` (the citing sentence) + `intents`
  (methodology/background/result) and writes `contexts/<seed>.md`, context-bearing papers
  first. Use it to upgrade novelty screening from regex-over-abstracts to *reading the
  sentence where a rival method is introduced*. (Not every citing paper exposes a
  sentence — the file reports how many did.)

  ```powershell
  PY $T --out $OUT contexts --seed DOI:10.24963/ijcai.2018/505 --limit 100
  ```

- **`search --bulk`** — an exhaustive coverage pass (up to 1,000/page, token-paginated,
  `--max` capped, **no** relevance ranking) for long-tail / "no paper in the field does X"
  claims, run *after* the ranked searches. **Bulk uses boolean query syntax, not fuzzy
  matching** — a plain multi-word phrase can match nothing. Use `+` (AND), `|` (OR), `-`
  (NOT), `"..."` (phrase):

  ```powershell
  # NOTE: PowerShell 5.1 mangles inner double-quotes passed to native exes. For phrase
  # queries use the Bash tool, or keep the query quote-free with '+'/'|' between words.
  PY $T --out $OUT search --bulk --query "load + forecasting + graph + convolutional" --max 200 --year-from 2021
  ```

## Synthesis (the part the researcher actually reads)

After the collection is built, read `papers.json` and write
`literature/<slug>/review.md`:

1. **Thematic groups** (4–7 themes), each theme a paragraph synthesizing what the
   grouped papers did — grounded strictly in their abstracts/tldrs, with inline
   `(FirstAuthor Year, cites N)` markers that map 1:1 to `papers.md` rows.
2. **Timeline of the field** — 3–6 sentences on how the approach evolved.
3. **Gaps & brainstorming ideas** — the deliverable for new-idea sessions: what the
   collected abstracts do NOT cover, contradictions between papers, and how the
   user's own project/manuscript relates to each gap. Label speculation clearly as
   *idea*, never as established finding.
4. If the input was a manuscript: a **"missing related work"** list — collected papers
   the manuscript's `.bib` does not already cite, each with one line on where it
   would fit.

## Practical notes

- **API key:** get a free key at https://www.semanticscholar.org/product/api and set it
  as a persistent `S2_API_KEY` environment variable (Windows: `setx S2_API_KEY <key>`);
  `lit_search.py` picks it up automatically. The key's limit is
  **1 request/second cumulative across all endpoints** — the script's `SLEEP = 1.2`
  already complies; never lower it below 1.0.
- **Attribution (license requirement):** any *published* material built on collections
  from this skill must cite Kinney et al., *The Semantic Scholar Open Data Platform*
  (2023, DOI 10.48550/arXiv.2301.10140), or credit Semantic Scholar. When writing a
  `review.md`, add an attribution note in the provenance header; the paper's verified
  entry can be added to any collection via
  `lookup --title "The Semantic Scholar Open Data Platform"`.
- **Rate limits (keyless fallback):** without the key, the public pool throttles hard;
  the script sleeps 1.2s between calls and retries 429s with backoff.
- **Windows:** set `PYTHONUTF8=1` (abstracts are unicode-heavy; cp1252 consoles crash).
- Batch multiple searches in one command line separated by `;` to keep prompts few.
- PDFs land only for open-access papers (plus arXiv fallback); paywalled ones keep
  DOI/URL entries so the user can fetch them via institutional access. Downloads are
  validated by `%PDF` magic bytes (publisher "OA" links are sometimes HTML landing
  pages — those are rejected and the arXiv mirror is tried next).
- Re-running any command is safe: the store dedupes by paperId and only adds.
- **`refresh` (citation counts):** re-fetches mutable fields (citationCount,
  influentialCitationCount, openAccessPdf, tldr) for the whole store in one batch POST
  per 500 ids, stamps each entry `_fetched: <ISO date>`, and preserves `_sources` +
  immutable fields. `report` then prints the as-of date in its header, so every "cites N"
  claim carries an implicit date. It flags any citation count that drops >20% (usually an
  S2 record merge/split, worth a look). Run it before a synthesis you'll quote counts from.
- **`enrich` (OpenAlex abstracts):** writes a **separate** `_abstract_openalex` field
  (tagged `_abstract_source: openalex`) — it never overwrites the S2 `abstract`, so
  provenance stays unambiguous. When you use an OpenAlex-sourced abstract in a review,
  attribute it "*(abstract via OpenAlex)*". Set `OPENALEX_MAILTO` (or `CROSSREF_MAILTO`)
  for the polite pool. Checkpoints every 10 entries (OpenAlex can drop mid-run).
  **Known limit:** some publishers (notably Elsevier) withhold abstracts from OpenAlex —
  the record exists but `abstract_inverted_index` is null, so those papers stay
  unenrichable. Screen them via `fulltext`/the DOI landing page, not `enrich`.

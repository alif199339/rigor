---
name: topic-watch
description: Re-run a lit-review collection's own recorded queries and diff for NEW papers published since it was built. Manual mode only -- reports new papers into watch_<date>.md (optionally merges them). Use to keep a literature survey current before a resubmission or revision. Scheduled/automatic weekly watching is NOT set up here (needs an always-on machine -- a separate user decision).
---

# /topic-watch — has anything new been published since the survey?

A literature collection goes stale the moment it's built. This re-runs the **collection's
own queries** (recovered from the `_sources` provenance tags the lit-review skill wrote)
against Semantic Scholar, biased toward recent years, and reports the papers that weren't
in the store — so you can refresh a survey before a revision without re-deriving queries.

The script is `.claude/skills/topic-watch/topic_watch.py`. It **imports** the lit-review
client (`../lit-review/lit_search.py`) for its API/rate-limit/store machinery, so the two
skills ship together.

```powershell
$env:PYTHONUTF8="1"
python .claude\skills\topic-watch\topic_watch.py --out literature\<slug>
#   --since-year 2025   only surface papers from this year on (default: last year)
#   --limit 30          results scanned per query
#   --merge             also add the new papers to papers.json (default: report only)
```

Output: `literature/<slug>/watch_<date>.md` — new papers with title, year, venue, cites,
DOI, tldr, and which query surfaced each. Every entry is a live S2 record (real by
construction, same integrity guarantee as lit-review).

## Manual only — scheduling is gated

This is **manual-first by design**. A weekly automatic watch needs an always-on machine
and a scheduler — a deliberate non-feature here. If you want it scheduled, wire your own
cron/Task-Scheduler job consciously; the tool itself never schedules anything. Running it
by hand before a revision is the supported path.

## Reading the result

- **Zero new papers** right after a collection is built is the **correct baseline** — it
  means the survey was current and future diffs have a clean reference point. It is a
  successful run, not a failure.
- New papers are report-only by default. Skim them; the ones worth keeping get folded in
  with `--merge` (which tags them `watch:<query>` in provenance), after which run
  `lit_search.py report` to regenerate `papers.md`, and optionally `enrich`/`pdfs`.
- Integrity carries over from lit-review: a paper may be cited only if it's in
  `papers.json` — so `--merge` (or a manual `lookup`) must happen before any new paper is
  discussed in a review.

## Relation to lit-review

`/lit-review` builds the collection; `/topic-watch` keeps it current. If the research
question itself changed (not just time passing), run `/lit-review` with fresh queries
instead — topic-watch only re-runs the queries already on record.

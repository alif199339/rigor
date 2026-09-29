---
name: pi-scout
description: Find prospective PIs / supervisors for a funded RA-ship or PhD, starting from YOUR OWN papers via the Semantic Scholar API. Harvests recent papers that cite or relate to your work, traces their authors, batch-fetches metrics (h-index, citations, affiliation leads), scores and shortlists PI-likely authors, then web-verifies each keeper (current position Asst-Prof+, university QS/THE tier + country, funding signals) and writes a per-PI dossier with a sharp audit of your odds of getting into the group. Use when asked to find potential supervisors, PIs, research groups, funded positions, or "who would hire me as an RA".
---

# /pi-scout — from your papers to a verified, audited list of prospective PIs

The insight this skill operationalizes: **the best prospective PI already knows your
work** — they cited it, or they publish in the exact space your papers define. So the
pipeline starts from *your* papers, not from a generic topic search, and ranks warm
leads (authors who cite you) above everyone else.

The tool is `.claude/skills/pi-scout/pi_scout.py` (stdlib-only; imports the lit-review
client for API/rate-limit machinery, so `.claude/skills/lit-review/` must be present).
The script does the S2 data plumbing; **you (the agent) own the judgment stages**:
web verification, tier assignment, fit matching, and the final audit.

## The non-negotiable integrity rules

1. **Every person and paper reported must come from the API this session** (i.e., be in
   `papers.json` / `authors.json`). Never add a "famous professor in this area" from
   model memory.
2. **S2 metrics are reported exactly as returned, with their fetch date.** Never
   estimate an h-index.
3. **Current position is a web-verified fact, never an inference.** S2 `affiliations`
   are sparse and stale; the script prints them as *leads*. A candidate may be called
   "Assistant Professor at X" only after you have seen it on a faculty/lab/staff page
   this session — and the dossier must record the **source URL + accessed date**.
4. **University ranks are looked up, never recalled.** Cite which ranking (QS or THE),
   which year, and the source. If unranked/unfindable, write "unranked/not found",
   never a guess.
5. **Funding claims need evidence** (a named grant, an openings page, a hiring post,
   with URL + date). "Probably has funding" without evidence must be labeled
   *speculation*.
6. Entry-probability verdicts are **informed judgment, clearly labeled as such** —
   grounded in the evidence in the dossier, never presented as fact.
7. Contact details come from **official pages only** (faculty page, lab site, paper
   corresponding-author footnote). Never guess an email pattern.

## Stage 0 — the matching baseline: `profile.yaml`

Everything downstream matches candidates against the *user's* profile. On first run,
create `<out>/profile.yaml` from their CV / conversation (ask only what you can't
infer). Template:

```yaml
name:
current_status:            # e.g. MSc student / research assistant / fresh graduate
own_papers:                # the seeds, as DOI:... / ARXIV:... (verified in Stage 1)
  - DOI:10....
research_keywords: []      # 4-8, concrete
methods_skills: []         # what a PI buys: tools, methods, domains, code
metrics: {citations: , h_index: }   # user's own, from their S2/Scholar page
target: funded RA / funded PhD / postdoc
constraints:
  countries_preferred: []
  countries_excluded: []
  earliest_start:
selling_points:            # awards, industry experience, OSS, datasets, teaching
```

## Stages 1–4 — the mechanical pipeline (script)

Default working folder `pi-scout/` at project root (`--out` to change). All commands
respect the 1 req/s key limit (sleep 1.2s built in).

```powershell
$env:PYTHONUTF8="1"
$T = ".claude/skills/pi-scout/pi_scout.py"

# 1. seed: register the user's OWN papers (journal + conference), verified via S2.
#    Prefer DOIs; --title uses the match endpoint. Confirm each hit IS their paper.
python $T seed --id DOI:10.1109/EXAMPLE.2024.123
python $T seed --title "Their conference paper title"

# 2. harvest: recent papers that cite the seeds (warm leads), recommendations
#    seeded on them, plus 2-4 topical searches from profile.yaml keywords.
#    Default window: last ~2 years (that's where open positions live).
python $T harvest --query "keyword phrase one" --query "keyword phrase two"

# 3. authors: batch-fetch the whole author pool's metrics (h-index, citations,
#    affiliation leads). Seeds' coauthors are excluded by default (known people).
python $T authors

# 4. shortlist: transparent additive score -> pi-scout/candidates.md + .json
python $T shortlist --top 40
```

Score components (printed per candidate, not a black box): **warm** (their papers cite
the user's), **senior** (last-author roles in the harvest — the PI signal), **breadth**,
**eminence** (h-index bands), **recency**. Flags: `likely-student` (drop),
`no-metrics` (verify manually), `superstar` (h≥55 — keep, but calibrate odds down:
biggest groups, most competition).

## Stage 5 — web verification (you, with WebSearch/WebFetch — the gate)

Take the top ~15–20 of `candidates.md` (skim the evidence blocks first; drop obvious
mismatches yourself). For each survivor, verify:

1. **Current position.** Search `"<name>" <affiliation-lead> faculty` / `"<name>"
   assistant professor`, open the faculty or lab page. Keep **Asst/Assoc/Full
   Professor or national equivalents** (UK/AU/NZ: Lecturer = Asst, Senior
   Lecturer/Reader = Assoc; Germany: W1/W2/W3 Professur; Netherlands: UD/UHD/HGL).
   Drop PhD students and postdocs; industry researchers only if they hold a funding-
   capable academic post too (flag it).
2. **Institution, country, and tier.** Look up the university's latest QS **or** THE
   world rank (say which, and the year):
   - **T1** = top 100 · **T2** = 101–300 · **T3** = 301–700 · **T4** = 701+/unranked.
   Note strong national systems explicitly (e.g. a T3-by-world-rank university that is
   top-5 nationally with rich funding is often a better RA bet than a T1 long shot).
   **Rank gate (default; profile.yaml `constraints.pi_filter` can override):** NEW
   candidates must sit at a QS ≤ 200 institution — *except* when the PI's own verified
   metrics are strong: **h ≥ 35 or ≥ 5,000 citations** on their *canonical* S2 profile.
   In-pool author records are often split fragments (h=3 for a chaired professor); before
   judging the exception, resolve the canonical record via
   `GET /graph/v1/author/search?query=<name>&fields=name,affiliations,hIndex,citationCount,paperCount`
   and cite those numbers. People already shortlisted in a previous pass are never
   retroactively dropped by a rule change — mark them grandfathered instead.
3. **Funding & hiring signals.** Lab page "openings"/"join us", recent grant
   announcements (NSF/ERC/EPSRC/national), a burst of new PhD students in recent
   author lists, posts on the lab news page. Every signal: URL + accessed date.

Honor `profile.yaml` country constraints here. Anyone who fails verification is
removed with one line saying why (e.g. "postdoc, not faculty — LinkedIn <url>").

## Stage 6 — dossiers + the audit (the deliverable)

```powershell
python $T dossier --author <authorId>   # one per verified keeper (~6-10 people)
```

The script writes `pi-scout/dossiers/<name>_<id>.md` with the grounded analytics
(trajectory, last-author share, venues, collaborators/group, most-cited + recent
papers, the harvest evidence connecting them to the user) and **two sections you must
fill**: the web-verification block (Stage 5 findings, with sources) and the audit:

- **Fit** — topic + methods overlap, citing concrete papers from the dossier; if they
  cited the user, quote *which* paper of theirs did.
- **What they'd value in the user** — map `profile.yaml` `methods_skills` /
  `selling_points` to visible gaps or directions in the PI's recent papers.
- **Funding likelihood** — from Stage-5 evidence only; label speculation.
- **Entry probability: High / Medium / Low** — the sharp reasoning: group size vs.
  intake, the user's metrics vs. the group's typical hire (check 1–2 current students'
  profiles if findable), warm-lead status, tier-vs-profile realism, timing.
- **Outreach angle** — the single concrete hook to open a cold email with (e.g. "your
  2025 paper X builds on my method Y; I can extend it to Z"). Do not draft or send
  anything unless asked.

## Stage 7 — final report

Write `pi-scout/report.md`: a table of the audited PIs **grouped by tier, then
country** — name, position, university (tier, rank source+year, country), h-index +
citations (as-of date), warm/cold, funding evidence (one phrase), entry verdict — each
row linking to its dossier. End with a ranked "apply order" (3–5 names) and one
paragraph of strategy (which tier band gives the user the best expected value, given
their profile). The report may contain **only** people whose dossier has a completed
web-verification block (integrity rule 3).

## Practical notes

- Needs `S2_API_KEY` (machine env; free at semanticscholar.org/product/api). Keyless
  works but throttles hard.
- Re-running any command is safe: stores dedupe by paperId/authorId and only add.
  `authors --refresh` refetches metrics; `harvest` again after adding a `seed` only
  adds the new seed's neighborhood.
- A seed with thousands of citations: raise `--limit-cites` (paginated) or tighten
  `--year-from` — recent citers are the ones hiring *now*.
- If the user has no indexed papers yet (common: everything under review), seed the
  **canonical papers of their niche** with `seed --anchor --id ...` instead: anchor
  authors stay in the candidate pool (they are prime candidates, not coauthors), and
  citers are labeled `cites-anchor` rather than `cites-you`. Pick anchors the user's
  manuscripts themselves build on (their references section names them), verify each
  via the API first, and say in the report that warmth is anchor-based, not
  citation-of-the-user. `--query`-only harvesting is the last resort (no warm signal
  at all).
- Same-name authors: S2 authorIds occasionally split/merge people. The dossier's
  trajectory histogram makes a wrong merge obvious (two careers glued together) —
  cross-check against the person's own publication page during Stage 5.
- Attribution: collections built on S2 data cite Kinney et al., *The Semantic Scholar
  Open Data Platform* (DOI 10.48550/arXiv.2301.10140) in anything published.
- This skill produces *research intelligence for the user's own outreach*. It never
  contacts anyone, and it uses only public, official sources.

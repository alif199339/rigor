# Contributing to RIGOR

Thanks for considering a contribution. RIGOR is small on purpose — each skill is one
`SKILL.md` (agent judgment + integrity rules) plus one script (mechanics). Contributions
should preserve that shape and the integrity guarantees.

## Ground rules (these are the product)

1. **Grounded by construction.** Anything that surfaces a citation/paper must route
   through a live API (Semantic Scholar, Crossref, OpenAlex) and land in a provenance-
   tagged store before it may be reported. No feature may introduce a path where model
   memory becomes a citation.
2. **Report, propose, never auto-edit.** Audit skills write reports; the human applies
   fixes. PRs that make `bib-audit`/`claims-audit` rewrite user files will be declined.
3. **Exact statistics.** `stat-check` reports n and exact p-values; non-significance is a
   result. No stars-only output, no silent dropping of null results.
4. **Rate-limit compliance.** Semantic Scholar: ≥1.0 s between calls (keyed). OpenAlex/
   Crossref: polite pool with a `mailto`. Don't lower the sleeps.
5. **No secrets in the repo.** Keys are environment variables; Kaggle tokens live in
   `~/.kaggle*`. The per-install `project_profile.yaml` / `studies.json` are git-ignored.

## Getting help

Questions about using or extending RIGOR are welcome. Open a
[Discussion](https://github.com/alif199339/rigor/discussions) for usage questions,
adoption reports, and design ideas, or an
[Issue](https://github.com/alif199339/rigor/issues) for bugs and suspected wrong
verdicts. There is no separate mailing list or chat — the tracker and Discussions are
the support channels, and the maintainer monitors both.

## Dev setup

```bash
python -m pip install pytest scipy pypdf pyyaml
pytest -q          # the suite is fully offline (HTTP is stubbed) and must stay that way
```

## PR checklist

- [ ] `pytest -q` green, no network calls added to tests
- [ ] new behavior documented in the relevant `SKILL.md` (and the manifest table if it's
      a new command/skill)
- [ ] scripts stay stdlib-first (scipy/pypdf only where already required)
- [ ] Layer-1/Layer-2 split respected: no project paths, names, or reference numbers in
      skills/scripts — those belong in `project_profile.yaml`/`studies.json`
- [ ] `skills/RESEARCH_AGENT.md` VERSION bumped + changelog line for user-visible changes

## Reporting issues

Use the issue tracker. For a suspected wrong verdict from an audit skill, include the
input entry (bib entry / claim context) and the report row — the classifiers are
deliberately conservative, and calibration reports are the most valuable issue type.

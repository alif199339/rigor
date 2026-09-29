---
name: ref-audit
description: Reviewer-side reference verification for submitted PDF papers (single PDF or a whole conference batch/zip) -- extract each paper's reference list, verify every entry against Semantic Scholar + Crossref + Retraction Watch, and flag HALLUCINATED (non-existent), retracted, and drifted citations with quoted evidence. Reuses bib-audit's engine; input is PDFs only, no .bib needed. Use for conference/journal screening, "check the references of these submitted papers", editorial integrity checks on accepted manuscripts.
---

# /ref-audit — do the cited works exist? (PDF-in, reviewer-side)

`/bib-audit` protects an **author's** `.bib`. This skill is its **reviewer-side**
twin: the input is a submitted paper's PDF (or a folder/zip of them), the reference
list is extracted and parsed from the PDF text, and every entry runs through the
**same verification engine** (imported from `bib-audit/bib_audit.py`, never forked).
A reference that resolves to no existing work is the classic fingerprint of
unverified AI-generated content — but the report states verifiable facts only;
what that implies about the authors is the technical chair's judgment, not ours.

The script is `ref_audit.py` (pypdf + the bib-audit folder beside this one;
`S2_API_KEY` respected at 1 req/s).

## Run it

```powershell
$env:PYTHONUTF8="1"
# a conference zip: unzip first, then batch (checkpointed -- safe to interrupt/resume)
Expand-Archive submissions.zip -DestinationPath papers\
python skills\ref-audit\ref_audit.py --dir papers\ --mailto <RIGOR_MAILTO>
# or a single paper:
python skills\ref-audit\ref_audit.py --pdf papers\<submission-id>.pdf --mailto <email>
```

Per paper: `<stem>_ref_audit.md` (ranked report) + `<stem>_ref_audit.json`
(machine-readable; doubles as the checkpoint — finished papers are skipped on
re-run unless `--force`). `--dir` also writes `ref_audit_summary.md` ranked
worst-first. Expect ~1–2 min per paper (S2 rate limit); a 50-paper batch is a
coffee-length run that resumes where it stopped.

## Verdicts

| Verdict | Meaning | Adjudication move |
|---|---|---|
| **RETRACTED** | Retraction Watch (via OpenAlex) marks the cited work retracted | mechanical, DOI-based — goes to the chair as-is |
| **NOT-FOUND** | no record in S2 or Crossref (a printed DOI/arXiv ID that resolves nowhere is noted — the hardest signal) | **the hallucination candidate** — re-check before confirming (below) |
| **MISMATCH** | a record exists but title/year conflicts beyond drift tolerance | check whether it's a wrong-edition match or genuine misquotation |
| **PARSE-FAILED** | the entry couldn't be parsed from PDF text | read the references pages in the actual PDF and verify by hand |
| UNVERIFIABLE / NON-PAPER-OK / VERIFIED | as in bib-audit | nothing, unless a diff is noted |

## The adjudication pass (yours — mandatory before anything reaches the chair)

A NOT-FOUND is a *candidate*: non-indexed national conferences, non-English
venues, brand-new preprints, standards, and theses are legitimate misses. For
**every** NOT-FOUND:

1. Web-search the exact printed title (and first author). Found on a publisher/
   venue page → **EXCLUDE** with the URL in the note. Truly nothing anywhere,
   or the printed DOI/arXiv ID resolves nowhere → **CONFIRM**.
2. For PARSE-FAILED, Read the PDF's reference pages directly and verify the
   entry yourself (CONFIRM only if it's genuinely untraceable).
3. Write `<stem>_adjudication.json` next to the PDF (merged with result-audit's
   adjudications in the same file):

```json
{ "refs": { "[17]": {"decision": "CONFIRM", "note": "no trace anywhere; printed DOI 10.1109/... resolves nowhere"},
            "[9]":  {"decision": "EXCLUDE", "note": "real Springer chapter, not indexed: <url>"} } }
```

Then compile the chair workbook:
`python skills\_shared\panel_compile.py --dir papers\ [--id-map ids.csv]` →
`PANEL_TRIAGE.xlsx` (Findings sheet: one row per exact finding, `Ref [n]` named;
Summary sheet: per-paper counts). Only CONFIRMed NOT-FOUNDs are labeled
HALLUCINATED-REF; unadjudicated ones are marked as such.

## Integrity rules (non-negotiable)

- **Confidentiality**: only the *cited works'* title/DOI/arXiv strings are sent
  to the APIs — metadata of already-published works. The submitted paper's own
  title, authors, abstract, and body never leave the machine. Do not paste
  submission content into web searches during adjudication either — search the
  *cited* titles only.
- **Facts, not attribution**: reports and the workbook state what is verifiable
  ("resolves to no record; printed arXiv ID does not exist") and never assert
  how the paper was written. AI-use judgment belongs to the technical chair.
- Never judge a reference from model memory — the API result (or the
  adjudication web check with its URL) is the only admissible evidence.
- A "to appear/submitted" note in the entry is recorded but never auto-softens
  a NOT-FOUND; the adjudication pass owns that call.
- The scripts report; they never modify the PDFs or write anything outside the
  papers directory.

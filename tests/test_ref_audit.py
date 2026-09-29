"""Offline tests for ref_audit.py: reference-list location, splitting, per-entry
parsing, the verdict paths through bib-audit's engine (HTTP stubbed), and the
checkpointed per-paper run."""
import json

import pytest


@pytest.fixture(autouse=True)
def _offline(bib, monkeypatch):
    # every network entry point of the shared engine answers "nothing found"
    # unless a test overrides it
    for name in ("s2_get", "s2_by_doi", "s2_by_title", "crossref_by_doi",
                 "crossref_by_title", "openalex_retracted"):
        monkeypatch.setattr(bib, name, lambda *a, **k: None)
    monkeypatch.setattr(bib, "url_alive", lambda u: False)


PAGES = [
    "A Paper About Things\nAbstract -- we study things.\nI. INTRODUCTION\nBody text [1].",
    "More body. This is discussed in the references section below.\n"
    "REFERENCES\n"
    "[1] A. Author, “A real study of graph networks for forecasting,” in Proc. ICML, 2020.\n"
    "[2] B. Writer, “An entirely fabricated survey of everything,” IEEE Trans. X, "
    "vol. 3, pp. 1–5, 2021, see also [7].\n"
    "[3] C. Person, “Preprint on something new,” arXiv:2401.01234, 2024.\n",
    "[4] D. Name, “Our own work, submitted for review,” IEEE Access, 2026 (submitted).\n",
]


def test_find_references_uses_last_heading_and_maps_pages(refa):
    text, base, page_of = refa.find_references(PAGES)
    assert text.lstrip().startswith("[1] A. Author")
    assert "references section below" not in text        # body mention is not a heading
    assert page_of(base + text.index("[4]")) == 3


def test_split_numbered_skips_inline_brackets(refa):
    text, _, _ = refa.find_references(PAGES)
    entries = refa.split_numbered(text)
    assert [n for n, _, _ in entries] == [1, 2, 3, 4]     # the inline [7] is not a split
    assert "see also [7]" in entries[1][2]


def test_split_author_start_fallback(refa):
    text = ("\nSmith, J. A study of load with a long enough title. J. Energy, 2019.\n"
            "Doe, A. Another paper on forecasting methods today. Proc. X, 2021.\n")
    parts = refa.split_author_start(text)
    assert len(parts) == 2


def test_parse_ref_quoted_ieee_entry(refa):
    pr = refa.parse_ref(2, refa.clean_entry(
        "[2] B. Writer, “An entirely fabricated sur-\nvey of everything,” IEEE Trans. X, "
        "vol. 3, pp. 1–5, 2021. doi: 10.1109/TX.2021.12345."))
    assert pr["title"] == "An entirely fabricated survey of everything"   # hyphenation undone
    assert pr["venue"] == "IEEE Trans. X"
    assert pr["year"] == "2021"
    assert pr["doi"] == "10.1109/TX.2021.12345"
    assert not pr["heuristic_title"]


def test_parse_ref_flags_unpublished_and_arxiv(refa):
    pr = refa.parse_ref(4, "[4] D. Name, “Our own work,” IEEE Access, 2026 (submitted).")
    assert pr["note"] and "submitted" in pr["note"]
    pr = refa.parse_ref(3, "[3] C. Person, “Preprint,” arXiv:2401.01234, 2024.")
    assert pr["arxiv"] == "2401.01234"


def test_parse_ref_unquoted_style_uses_heuristic_title(refa):
    pr = refa.parse_ref(1, "[1] J. Smith and A. Doe. Learning the structure of regional "
                           "demand from data. Energy Reports, 7:1–9, 2021.")
    assert pr["heuristic_title"]
    assert pr["title"].startswith("Learning the structure of regional demand")


def test_audit_ref_parse_failed_when_nothing_parseable(refa):
    r = refa.audit_ref(refa.parse_ref(5, "[5] X. Y, Z."))
    assert r["verdict"] == "PARSE-FAILED"


def test_audit_ref_printed_doi_that_resolves_nowhere(refa):
    r = refa.audit_ref(refa.parse_ref(6, "[6] doi: 10.9999/does.not.exist"))
    assert r["verdict"] == "NOT-FOUND"
    assert any("resolves in neither" in n for n in r["notes"])


def test_audit_ref_nonresolving_arxiv_is_hard_signal(refa, bib, monkeypatch):
    monkeypatch.setattr(bib, "audit_entry", lambda e: {
        "verdict": "NOT-FOUND", "record": None, "diffs": [], "suggest": [],
        "notes": ["no record in S2 or Crossref"]})
    r = refa.audit_ref(refa.parse_ref(3, "[3] C. Person, “A preprint that does not exist "
                                         "anywhere,” arXiv:2401.99999, 2024."))
    assert r["verdict"] == "NOT-FOUND"
    assert any("does NOT resolve" in n for n in r["notes"])


def test_audit_ref_arxiv_resolving_to_other_title_is_mismatch(refa, bib, monkeypatch):
    monkeypatch.setattr(bib, "s2_get", lambda path: {
        "title": "Completely Unrelated Chemistry Paper", "year": 2024,
        "externalIds": {"ArXiv": "2401.01234"}})
    r = refa.audit_ref(refa.parse_ref(3, "[3] C. Person, “Graph networks for load "
                                         "forecasting,” arXiv:2401.01234, 2024."))
    assert r["verdict"] == "MISMATCH"
    assert r["diffs"] and r["diffs"][0][0] == "title"


def test_audit_ref_retracted_via_arxiv_record_doi(refa, bib, monkeypatch):
    monkeypatch.setattr(bib, "s2_get", lambda path: {
        "title": "Graph networks for load forecasting", "year": 2024,
        "externalIds": {"ArXiv": "2401.01234", "DOI": "10.1/retracted"}})
    monkeypatch.setattr(bib, "openalex_retracted", lambda doi: True)
    r = refa.audit_ref(refa.parse_ref(3, "[3] C. Person, “Graph networks for load "
                                         "forecasting,” arXiv:2401.01234, 2024."))
    assert r["verdict"] == "RETRACTED"
    assert "RETRACTED" in r["notes"][0]


def test_audit_ref_damaged_doi_retried_by_title(refa, bib, monkeypatch):
    calls = []

    def fake_audit(entry):
        calls.append(dict(entry["fields"]))
        if "doi" in entry["fields"]:
            return {"verdict": "NOT-FOUND", "record": None, "diffs": [], "suggest": [],
                    "notes": ["DOI resolves in neither S2 nor Crossref -- check the DOI string."]}
        return {"verdict": "VERIFIED", "record": {"src": "S2", "title": "t"}, "diffs": [],
                "suggest": [], "notes": []}

    monkeypatch.setattr(bib, "audit_entry", fake_audit)
    r = refa.audit_ref(refa.parse_ref(2, "[2] B. Writer, “A real survey of load forecasting "
                                         "methods,” IEEE Trans. X, 2021, doi: 10.1109/TX.20"))
    assert r["verdict"] == "VERIFIED"
    assert len(calls) == 2 and "doi" not in calls[1]          # second try is title-only
    assert any("retried by title match" in n for n in r["notes"])


def test_audit_pdf_checkpoints_and_summarises(refa, bib, tmp_path, monkeypatch):
    def fake_audit(entry):
        bad = "fabricated" in entry["fields"]["title"]
        return {"verdict": "NOT-FOUND" if bad else "VERIFIED",
                "record": None if bad else {"src": "S2", "title": entry["fields"]["title"],
                                            "year": 2020, "venue": "V"},
                "diffs": [], "suggest": [], "notes": []}

    monkeypatch.setattr(bib, "audit_entry", fake_audit)
    monkeypatch.setattr(refa, "extract_pages", lambda p: PAGES)
    pdf = tmp_path / "p01.pdf"
    doc = refa.audit_pdf(str(pdf))
    assert doc["complete"] and doc["style"] == "ieee-numbered"
    assert doc["n_entries"] == 4
    verdicts = {r["key"]: r["verdict"] for r in doc["results"]}
    assert verdicts["[2]"] == "NOT-FOUND"
    md = (tmp_path / "p01_ref_audit.md").read_text(encoding="utf-8")
    assert "| [2] | **NOT-FOUND** |" in md
    assert "never the paper's own text" in md              # confidentiality statement

    # checkpoint: a completed JSON is skipped without re-extracting the PDF
    monkeypatch.setattr(refa, "extract_pages",
                        lambda p: (_ for _ in ()).throw(AssertionError("re-extracted")))
    again = refa.audit_pdf(str(pdf))
    assert again["n_entries"] == 4

    refa.write_summary(str(tmp_path))
    summ = (tmp_path / "ref_audit_summary.md").read_text(encoding="utf-8")
    assert "**p01**" in summ                                # NOT-FOUND -> bolded row


def test_audit_pdf_without_reference_list_is_parse_failed(refa, tmp_path, monkeypatch):
    monkeypatch.setattr(refa, "extract_pages", lambda p: ["Just a title.", "No refs at all."])
    doc = refa.audit_pdf(str(tmp_path / "empty.pdf"))
    assert doc["results"][0]["verdict"] == "PARSE-FAILED"
    saved = json.loads((tmp_path / "empty_ref_audit.json").read_text(encoding="utf-8"))
    assert saved["complete"]

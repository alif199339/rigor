"""Offline tests for result_audit.py: section mapping, the seven internal-consistency
checks (each with a planted failure and a clean control), and the per-paper run."""
import json

import pytest

# a synthetic three-page paper with one planted instance of each failure class
PAGES = [
    "A Study of Things\n"
    "Abstract—We propose a model that achieves an accuracy of 95.2% on the benchmark.\n"
    "Index Terms—things, stuff\n"
    "I. INTRODUCTION\n"
    "Prior work [3] reports lower numbers in 2019.",

    "II. RESULTS\n"
    "TABLE II: Accuracy on the benchmark.\n"
    "Ours 94.8 0.912.\n"
    "Baseline 90.1 0.870.\n"
    "The data are split 70/20/20 into train, validation and test sets.\n"
    "We observe p = 1.3 for the ablation.\n"
    "The mean = 3.47 (N = 10) participants rated it.",

    "V. CONCLUSION\n"
    "Our model reaches an accuracy of 96.1% overall.\n"
    "ACKNOWLEDGMENT\n"
    "We thank everyone.\n"
    "REFERENCES\n"
    "[1] A. B, “x,” 2020.",
]


def L(sec, *lines, pg=1):
    return [(pg, sec, s) for s in lines]


def test_sectioned_lines_state_machine(resa):
    lines = resa.sectioned_lines(PAGES)
    secs = {}
    for pg, sec, line in lines:
        secs.setdefault(sec, []).append(line)
    assert any("95.2%" in s for s in secs["abstract"])
    assert any("70/20/20" in s for s in secs["body"])
    assert any("96.1%" in s for s in secs["conclusion"])
    assert any("thank" in s for s in secs["back"])
    assert not any("[1] A. B" in s for _, _, s in lines)    # stops at REFERENCES


def test_conclusion_word_in_body_sentence_does_not_flip_section(resa):
    pages = ["Abstract—x.\nI. INTRODUCTION\nIn conclusion, prior work is limited.\nMore body."]
    secs = {sec for _, sec, _ in resa.sectioned_lines(pages)}
    assert "conclusion" not in secs


def test_scrub_removes_citations_years_and_table_refs(resa):
    s = resa.scrub("As in [3, 7] and Table IV (2021), accuracy is 91.5%.")
    assert "91.5" in s
    assert "3" not in s.split("91.5")[0] and "2021" not in s


def test_classify_matched_near_miss_orphan(resa):
    pool = [("94.8", 94.8), ("0.912", 0.912)]
    assert resa.classify({"tok": "94.8", "val": 94.8}, pool)[0] == "MATCHED"
    assert resa.classify({"tok": "95.2", "val": 95.2}, pool)[0] == "NEAR-MISS"
    assert resa.classify({"tok": "50.5", "val": 50.5}, pool)[0] == "ORPHAN"
    assert resa.classify({"tok": "50.5", "val": 50.5}, [])[0] == "ORPHAN"


def test_arith_flags_underivable_change_and_accepts_derivable(resa):
    bad = L("body", "Accuracy rises from 80.0% to 88.0%, an improvement of 25%.")
    good = L("body", "Accuracy rises from 80.0% to 88.0%, an improvement of 10%.")
    pp = L("body", "Accuracy rises from 80.0% to 88.0%, a gain of 8% points.")
    assert len(resa.check_arith(bad)) == 1
    assert resa.check_arith(good) == []                        # relative reading
    assert resa.check_arith(pp) == []                          # percentage-point reading


def test_absolute_metric_values_are_not_change_claims(resa):
    assert resa.change_claims("Baseline-X (12.4%) and ours (10.1%).") == []
    assert sorted(resa.change_claims("It is 20% faster and reduces error by 5%.")) == [5.0, 20.0]


def test_split_sum(resa):
    assert len(resa.check_splits(L("body", "We use a 70/20/20 train/val/test split."))) == 1
    assert resa.check_splits(L("body", "We use a 70/15/15 train/val/test split.")) == []


def test_impossible_statistics(resa):
    out = resa.check_impossible(L(
        "body",
        "We observe p = 1.3 here. The accuracy of 104.2% is reported. "
        "Correlation r = 1.2 holds. The RMSE of -0.3 is achieved. Finally p = 0 exactly."))
    whats = " ".join(o["what"] for o in out)
    assert "outside (0, 1]" in whats
    assert "exceeds 100%" in whats
    assert "outside [-1, 1]" in whats
    assert "is negative" in whats
    assert "p = 0 exactly" in whats


def test_scientific_notation_p_is_not_impossible(resa):
    ok = L("body", "The effect is strong (p = 5.5×10−169). Also p < 10 −11 and p = 3e-8 hold.")
    assert resa.check_impossible(ok) == []


def test_statcheck_recomputation(resa):
    pytest.importorskip("scipy")
    if resa._sst is None:
        pytest.skip("result_audit loaded without scipy")
    bad = resa.check_impossible(L("body", "The difference holds, t(20) = 2.1, p = 0.40."))
    good = resa.check_impossible(L("body", "The difference holds, t(20) = 2.1, p = 0.049."))
    assert len(bad) == 1 and "two-tailed p" in bad[0]["what"]
    assert good == []


def test_grim(resa):
    assert len(resa.check_grim(L("body", "The mean = 3.47 (N = 10) held."))) == 1
    assert resa.check_grim(L("body", "The mean = 3.40 (N = 10) held.")) == []


def test_xsection(resa):
    lines = (L("abstract", "It achieves an accuracy of 95.2% overall.") +
             L("conclusion", "It reaches an accuracy of 96.1% overall.", pg=3))
    out = resa.check_xsection(lines)
    assert len(out) == 1 and out[0]["metric"] == "accuracy"
    agree = (L("abstract", "An accuracy of 95.2% overall.") +
             L("conclusion", "An accuracy of 95.2% overall.", pg=3))
    assert resa.check_xsection(agree) == []


def test_audit_pdf_end_to_end(resa, tmp_path, monkeypatch):
    monkeypatch.setattr(resa, "extract_pages", lambda p: PAGES)
    doc = resa.audit_pdf(str(tmp_path / "p07.pdf"))
    checks = {c["check"] for c in doc["candidates"]}
    assert {"NEAR-MISS", "XSECTION", "SPLIT", "IMPOSSIBLE", "GRIM"} <= checks
    assert [c["id"] for c in doc["candidates"]] == \
        [f"C{i}" for i in range(1, len(doc["candidates"]) + 1)]     # stable ids
    saved = json.loads((tmp_path / "p07_result_audit.json").read_text(encoding="utf-8"))
    assert saved["complete"] and saved["n_claims"] >= 2
    md = (tmp_path / "p07_result_audit.md").read_text(encoding="utf-8")
    assert "| NEAR-MISS |" in md and "adjudication" in md

    resa.write_summary(str(tmp_path))
    summ = (tmp_path / "result_audit_summary.md").read_text(encoding="utf-8")
    assert "**p07**" in summ                                    # has high-severity items


def test_missing_abstract_is_warned(resa, tmp_path, monkeypatch):
    monkeypatch.setattr(resa, "extract_pages", lambda p: ["Some text with 91.5% only."])
    resa.audit_pdf(str(tmp_path / "noabs.pdf"))
    md = (tmp_path / "noabs_result_audit.md").read_text(encoding="utf-8")
    assert "no Abstract section was detected" in md

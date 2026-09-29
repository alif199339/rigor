"""Offline tests for _shared/panel_compile.py: the inclusion rules that decide what
reaches the chair's workbook (adjudication-gated), the ID map, and both writers."""
import csv
import json

import pytest


def _ref(key, verdict, page=9, raw=None):
    return {"key": key, "num": int(key.strip("[]")), "verdict": verdict, "page": page,
            "raw": raw or f"{key} some printed reference", "record": None,
            "diffs": [], "notes": [f"{verdict} note"]}


@pytest.fixture
def audited(tmp_path):
    d = tmp_path / "papers"
    d.mkdir()
    (d / "p1_ref_audit.json").write_text(json.dumps({"paper_id": "p1", "results": [
        _ref("[1]", "VERIFIED"), _ref("[2]", "NOT-FOUND"), _ref("[3]", "NOT-FOUND"),
        _ref("[4]", "MISMATCH"), _ref("[5]", "RETRACTED"), _ref("[6]", "PARSE-FAILED"),
        _ref("[7]", "UNVERIFIABLE")]}), encoding="utf-8")
    (d / "p1_result_audit.json").write_text(json.dumps({"paper_id": "p1", "candidates": [
        {"id": "C1", "check": "NEAR-MISS", "page": 1, "detail": "abstract 95.2 vs 94.8",
         "ctx": "achieves 95.2%", "severity_hint": "high"},
        {"id": "C2", "check": "ORPHAN", "page": 1, "detail": "orphan", "ctx": "x",
         "severity_hint": "medium"},
        {"id": "C3", "check": "SPLIT", "page": 2, "detail": "split", "ctx": "y",
         "severity_hint": "medium"}]}), encoding="utf-8")
    (d / "p1_adjudication.json").write_text(json.dumps({
        "refs": {"[2]": {"decision": "CONFIRM", "note": "no trace anywhere"},
                 "[4]": {"decision": "EXCLUDE", "note": "wrong-edition match"}},
        "result": {"C1": {"decision": "CONFIRMED-INCONSISTENT",
                          "note": "abstract p.1 '95.2' vs Table III '94.8'"},
                   "C2": {"decision": "EXTRACTION-NOISE", "note": "reflow"}}}),
        encoding="utf-8")
    # a second paper audited by result-audit only, nothing adjudicated
    (d / "p2_result_audit.json").write_text(json.dumps({"paper_id": "p2", "candidates": [
        {"id": "C1", "check": "ARITH", "page": 3, "detail": "a", "ctx": "b",
         "severity_hint": "high"}]}), encoding="utf-8")
    return d


def test_inclusion_rules(panel, audited):
    findings, summary = panel.compile_dir(str(audited), {})
    p1 = [f for f in findings if f[0] == "p1"]
    types = [f[2] for f in p1]
    # severity-ordered: retracted, hallucinated, data, unadjudicated not-found
    assert types == ["RETRACTED-REF", "HALLUCINATED-REF", "DATA-INCONSISTENCY",
                     "NOT-FOUND-REF (unadjudicated)"]
    assert not any("Ref [4]" in f[3] for f in p1)                    # EXCLUDE drops it
    assert not any("Ref [6]" in f[3] or "Ref [7]" in f[3] for f in p1)
    hall = next(f for f in p1 if f[2] == "HALLUCINATED-REF")
    assert hall[3] == "Ref [2], p.9" and hall[4].startswith("[2] some printed")
    assert "adjudication: no trace anywhere" in hall[5]
    assert not any(f[0] == "p2" for f in findings)       # unadjudicated result -> no row

    row = {r[0]: r for r in summary}
    #          refs ver hall nf retr mm pf conf unadj
    assert row["p1"][2:] == [7, 1, 1, 1, 1, 0, 1, 1, 1]
    assert row["p2"][2:] == [0, 0, 0, 0, 0, 0, 0, 0, 1]
    assert summary[0][0] == "p1"                          # worst paper ranked first


def test_id_map_relabels_papers(panel, audited, tmp_path):
    m = tmp_path / "ids.csv"
    m.write_text("filename,paper_id,title\np1.pdf,SUB-001,Some title\np2.pdf,SUB-002,\n",
                 encoding="utf-8")
    ids = panel.load_id_map(str(m))
    assert ids == {"p1": "SUB-001", "p2": "SUB-002"}                 # header row skipped
    findings, summary = panel.compile_dir(str(audited), ids)
    assert {r[0] for r in summary} == {"SUB-001", "SUB-002"}
    assert all(f[1] == "p1.pdf" for f in findings)                   # file kept alongside


def test_csv_fallback_writer(panel, audited, tmp_path):
    findings, summary = panel.compile_dir(str(audited), {})
    out = tmp_path / "PANEL_TRIAGE.xlsx"
    panel.write_csv(findings, summary, str(out))
    with open(tmp_path / "PANEL_TRIAGE_findings.csv", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == panel.FINDINGS_HEADER and len(rows) == 1 + len(findings)
    assert (tmp_path / "PANEL_TRIAGE_summary.csv").exists()


def test_xlsx_writer(panel, audited, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    findings, summary = panel.compile_dir(str(audited), {})
    out = tmp_path / "PANEL_TRIAGE.xlsx"
    panel.write_xlsx(findings, summary, str(out))
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == ["Findings", "Summary"]
    assert wb["Findings"].max_row == 1 + len(findings)
    note = [c.value for c in wb["Summary"]["A"] if c.value][-1]
    assert "attribution and the final decision belong to the technical chair" in note

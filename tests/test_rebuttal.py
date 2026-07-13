"""Offline tests for rebuttal.py: import/split, respond, and diff-verified checks."""
import json

import pytest


def run(rebut, monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["rebuttal.py", *argv])
    rebut.main()


REVIEWS = """Dear authors, thanks for the submission.

1. The claim that the compact model always wins is not supported at the full rung.

2. Please report effect sizes, not only p-values.

3) Figure 3 is unreadable in grayscale.
"""

DIFF = """--- a/main.tex
+++ b/main.tex
@@ -10,3 +10,4 @@
-always wins
+wins in the scarce-data regime
+We report Cohen's dz alongside every p-value.
"""


def seed(rebut, monkeypatch, tmp_path):
    d = str(tmp_path / "reb")
    (tmp_path / "reviews.txt").write_text(REVIEWS, encoding="utf-8")
    run(rebut, monkeypatch, "--dir", d, "init")
    run(rebut, monkeypatch, "--dir", d, "import", "--file",
        str(tmp_path / "reviews.txt"), "--reviewer", "R1")
    return d


def test_import_splits_numbered_comments(rebut, monkeypatch, tmp_path):
    d = seed(rebut, monkeypatch, tmp_path)
    data = json.load(open(f"{d}/comments.json", encoding="utf-8"))
    texts = [c["text"] for c in data["comments"]]
    assert len(texts) == 4                              # greeting para + 3 numbered
    assert any("grayscale" in t for t in texts)
    assert [c["id"] for c in data["comments"]] == ["R1.1", "R1.2", "R1.3", "R1.4"]


def test_respond_and_check_verified_change(rebut, monkeypatch, tmp_path, capsys):
    d = seed(rebut, monkeypatch, tmp_path)
    (tmp_path / "rev.patch").write_text(DIFF, encoding="utf-8")
    run(rebut, monkeypatch, "--dir", d, "respond", "R1.2", "--action", "change",
        "--text", "Softened the claim.", "--anchors", "main.tex",
        "--quote", "wins in the scarce-data regime")
    run(rebut, monkeypatch, "--dir", d, "respond", "R1.3", "--action", "change",
        "--text", "Added dz.", "--anchors", "main.tex",
        "--quote", "Cohen's dz alongside every p-value")
    run(rebut, monkeypatch, "--dir", d, "respond", "R1.4", "--action", "clarify",
        "--text", "The camera-ready uses the colorblind-safe palette.")
    run(rebut, monkeypatch, "--dir", d, "respond", "R1.1", "--action", "decline",
        "--text", "This is a summary paragraph, not a request.")
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        run(rebut, monkeypatch, "--dir", d, "check",
            "--diff-file", str(tmp_path / "rev.patch"))
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "R1.2: change verified" in out and "0 failure(s)" in out


def test_check_fails_on_claimed_but_no_diff(rebut, monkeypatch, tmp_path, capsys):
    d = seed(rebut, monkeypatch, tmp_path)
    (tmp_path / "rev.patch").write_text(DIFF, encoding="utf-8")
    # claims to have changed appendix.tex, which the diff never touches
    run(rebut, monkeypatch, "--dir", d, "respond", "R1.2", "--action", "change",
        "--text", "Revised the appendix accordingly.", "--anchors", "appendix.tex")
    for cid in ("R1.1", "R1.3", "R1.4"):
        run(rebut, monkeypatch, "--dir", d, "respond", cid, "--action", "clarify",
            "--text", "x")
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        run(rebut, monkeypatch, "--dir", d, "check",
            "--diff-file", str(tmp_path / "rev.patch"))
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "CLAIMED-BUT-NOT-VERIFIED" in out and "appendix.tex" in out


def test_check_fails_on_unanswered_and_quote_missing(rebut, monkeypatch,
                                                     tmp_path, capsys):
    d = seed(rebut, monkeypatch, tmp_path)
    (tmp_path / "rev.patch").write_text(DIFF, encoding="utf-8")
    run(rebut, monkeypatch, "--dir", d, "respond", "R1.2", "--action", "change",
        "--text", "x", "--anchors", "main.tex", "--quote", "text that is nowhere")
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        run(rebut, monkeypatch, "--dir", d, "check",
            "--diff-file", str(tmp_path / "rev.patch"))
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "UNANSWERED" in out and "quote not found" in out


def test_unverifiable_change_warns_at_respond_time(rebut, monkeypatch,
                                                   tmp_path, capsys):
    d = seed(rebut, monkeypatch, tmp_path)
    run(rebut, monkeypatch, "--dir", d, "respond", "R1.2", "--action", "change",
        "--text", "We fixed it, trust us.")
    assert "cannot be verified" in capsys.readouterr().out


def test_compile_letter_groups_and_flags_pending(rebut, monkeypatch, tmp_path):
    d = seed(rebut, monkeypatch, tmp_path)
    run(rebut, monkeypatch, "--dir", d, "respond", "R1.2", "--action", "clarify",
        "--text", "Answered inline.")
    run(rebut, monkeypatch, "--dir", d, "compile")
    md = open(f"{d}/RESPONSE.md", encoding="utf-8").read()
    assert "## Reviewer R1" in md
    assert "Answered inline." in md
    assert md.count("[RESPONSE PENDING]") == 3

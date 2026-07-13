"""Offline tests for lab-notebook: append-only log, track lifecycle, the status
digest, compile (superseded markers), and the check-narrative citation guardrail."""
import json

import pytest


def run(labnb, monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["notebook.py", *argv])
    labnb.main()


def seed(labnb, monkeypatch, d):
    run(labnb, monkeypatch, "--dir", d, "init", "--name", "t")
    run(labnb, monkeypatch, "--dir", d, "track-add", "1A", "Parse")
    run(labnb, monkeypatch, "--dir", d, "track-add", "2A", "Model", "--depends", "1A")


def entries(tmp_path):
    p = tmp_path / "nb" / "entries.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l]


def test_init_is_refused_on_existing_notebook(labnb, tmp_path, monkeypatch):
    d = str(tmp_path / "nb")
    run(labnb, monkeypatch, "--dir", d, "init")
    with pytest.raises(SystemExit):
        run(labnb, monkeypatch, "--dir", d, "init")


def test_track_add_dup_refused_unknown_dep_warns(labnb, tmp_path, monkeypatch, capsys):
    d = str(tmp_path / "nb")
    seed(labnb, monkeypatch, d)
    with pytest.raises(SystemExit):
        run(labnb, monkeypatch, "--dir", d, "track-add", "1A", "dup")
    run(labnb, monkeypatch, "--dir", d, "track-add", "3A", "X", "--depends", "9Z")
    assert "dependency '9Z' is not (yet) a known track" in capsys.readouterr().out


def test_log_appends_sequential_ids_one_json_per_line(labnb, tmp_path, monkeypatch):
    d = str(tmp_path / "nb")
    seed(labnb, monkeypatch, d)
    for i in range(3):
        run(labnb, monkeypatch, "--dir", d, "log", "1A",
            "--type", "progress", "--text", f"step {i}")
    es = entries(tmp_path)
    assert [e["id"] for e in es] == [1, 2, 3]
    assert all(e["track"] == "1A" and e["type"] == "progress" for e in es)


def test_finding_without_evidence_warns(labnb, tmp_path, monkeypatch, capsys):
    d = str(tmp_path / "nb")
    seed(labnb, monkeypatch, d)
    run(labnb, monkeypatch, "--dir", d, "log", "1A",
        "--type", "finding", "--text", "unsupported")
    assert "FINDING without --evidence is an assertion" in capsys.readouterr().out
    ev = tmp_path / "t1.csv"
    ev.write_text("T1,42.7", encoding="utf-8")
    run(labnb, monkeypatch, "--dir", d, "log", "1A",
        "--type", "finding", "--text", "T1 mean = 42.7", "--evidence", str(ev))
    assert "warn" not in capsys.readouterr().out


def test_missing_evidence_path_warns(labnb, tmp_path, monkeypatch, capsys):
    d = str(tmp_path / "nb")
    seed(labnb, monkeypatch, d)
    run(labnb, monkeypatch, "--dir", d, "log", "1A",
        "--type", "finding", "--text", "x", "--evidence", str(tmp_path / "ghost.csv"))
    assert "evidence path does not exist" in capsys.readouterr().out


def test_correction_warns_without_refs_and_on_unknown_ref(labnb, tmp_path,
                                                          monkeypatch, capsys):
    d = str(tmp_path / "nb")
    seed(labnb, monkeypatch, d)
    run(labnb, monkeypatch, "--dir", d, "log", "1A",
        "--type", "correction", "--text", "no anchor")
    assert "CORRECTION should point at the entry it supersedes" in \
        capsys.readouterr().out
    run(labnb, monkeypatch, "--dir", d, "log", "1A",
        "--type", "correction", "--text", "phantom", "--refs", "77")
    assert "--refs 77: no such entry id" in capsys.readouterr().out


def test_blocked_with_note_reaches_the_digest(labnb, tmp_path, monkeypatch, capsys):
    d = str(tmp_path / "nb")
    seed(labnb, monkeypatch, d)
    run(labnb, monkeypatch, "--dir", d, "track-set", "2A",
        "--status", "blocked", "--note", "API 403s since Friday")
    assert any(e["type"] == "blocker" and e["text"] == "API 403s since Friday"
               for e in entries(tmp_path))
    capsys.readouterr()
    run(labnb, monkeypatch, "--dir", d, "status")
    out = capsys.readouterr().out
    assert "open blockers" in out and "2A: API 403s since Friday" in out


def test_blocked_without_note_warns(labnb, tmp_path, monkeypatch, capsys):
    d = str(tmp_path / "nb")
    seed(labnb, monkeypatch, d)
    run(labnb, monkeypatch, "--dir", d, "track-set", "1A", "--status", "blocked")
    assert "log a `blocker` entry saying why" in capsys.readouterr().out


def test_status_marks_stale_next_steps(labnb, tmp_path, monkeypatch, capsys):
    d = str(tmp_path / "nb")
    seed(labnb, monkeypatch, d)
    run(labnb, monkeypatch, "--dir", d, "log", "1A", "--type", "next",
        "--text", "rerun the export")
    run(labnb, monkeypatch, "--dir", d, "log", "1A", "--type", "progress",
        "--text", "worked past it")
    capsys.readouterr()
    run(labnb, monkeypatch, "--dir", d, "status")
    assert "(stale? later entries exist)" in capsys.readouterr().out


def test_compile_marks_superseded_entries(labnb, tmp_path, monkeypatch):
    d = str(tmp_path / "nb")
    seed(labnb, monkeypatch, d)
    run(labnb, monkeypatch, "--dir", d, "log", "1A", "--type", "finding",
        "--text", "mean = 42.7")
    run(labnb, monkeypatch, "--dir", d, "log", "1A", "--type", "correction",
        "--text", "mean is 42.9 (recomputed)", "--refs", "1")
    run(labnb, monkeypatch, "--dir", d, "compile")
    md = (tmp_path / "nb" / "NOTEBOOK.md").read_text(encoding="utf-8")
    assert "**[superseded by #2]**" in md
    assert "## Track summary" in md and "## Session index" in md


def test_check_narrative_pass_fail_and_stale_warn(labnb, tmp_path, monkeypatch,
                                                  capsys):
    d = str(tmp_path / "nb")
    seed(labnb, monkeypatch, d)
    run(labnb, monkeypatch, "--dir", d, "log", "1A", "--type", "finding",
        "--text", "mean = 42.7")
    run(labnb, monkeypatch, "--dir", d, "log", "1A", "--type", "correction",
        "--text", "mean is 42.9", "--refs", "1")
    story = tmp_path / "NARRATIVE.md"

    story.write_text("The recomputed mean is 42.9 (#2).", encoding="utf-8")
    run(labnb, monkeypatch, "--dir", d, "check-narrative", str(story))
    assert "every citation resolves" in capsys.readouterr().out

    story.write_text("Mean is 42.7 (#1). Also #55.", encoding="utf-8")
    with pytest.raises(SystemExit):
        run(labnb, monkeypatch, "--dir", d, "check-narrative", str(story))
    out = capsys.readouterr().out
    assert "cites #55 -- no such entry" in out
    assert "cites #1, superseded by #2" in out

    story.write_text("A story with no anchors.", encoding="utf-8")
    with pytest.raises(SystemExit):
        run(labnb, monkeypatch, "--dir", d, "check-narrative", str(story))
    assert "cites no entries at all" in capsys.readouterr().out


def test_check_narrative_lists_uncited_findings(labnb, tmp_path, monkeypatch, capsys):
    d = str(tmp_path / "nb")
    seed(labnb, monkeypatch, d)
    run(labnb, monkeypatch, "--dir", d, "log", "1A", "--type", "finding",
        "--text", "used")
    run(labnb, monkeypatch, "--dir", d, "log", "2A", "--type", "finding",
        "--text", "never mentioned")
    story = tmp_path / "NARRATIVE.md"
    story.write_text("Only cites the first finding (#1).", encoding="utf-8")
    capsys.readouterr()
    run(labnb, monkeypatch, "--dir", d, "check-narrative", str(story))
    assert "1 finding(s) the narrative never cites: #2" in capsys.readouterr().out


def test_status_survives_hand_damaged_tracks(labnb, tmp_path, monkeypatch, capsys):
    d = str(tmp_path / "nb")
    seed(labnb, monkeypatch, d)
    run(labnb, monkeypatch, "--dir", d, "log", "1A", "--type", "next", "--text", "x")
    tp = tmp_path / "nb" / "tracks.json"
    meta = json.loads(tp.read_text(encoding="utf-8"))
    meta["tracks"][1]["status"] = "paused"      # bogus status
    del meta["tracks"][0]                        # entries still reference 1A
    tp.write_text(json.dumps(meta), encoding="utf-8")
    capsys.readouterr()
    run(labnb, monkeypatch, "--dir", d, "status")   # must not raise
    assert "PAUSED" in capsys.readouterr().out


def test_log_rejects_status_type_and_unknown_track(labnb, tmp_path, monkeypatch):
    d = str(tmp_path / "nb")
    seed(labnb, monkeypatch, d)
    with pytest.raises(SystemExit):
        run(labnb, monkeypatch, "--dir", d, "log", "1A", "--type", "status",
            "--text", "x")
    with pytest.raises(SystemExit):
        run(labnb, monkeypatch, "--dir", d, "log", "9Z", "--type", "progress",
            "--text", "x")

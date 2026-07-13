"""Offline tests for submit_gate.py: the check battery (fake steps), freeze,
and verify-freeze. No network, no real tools."""
import json
import sys

import pytest


def run(gate, monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["submit_gate.py", *argv])
    gate.main()


def gate_yaml(tmp_path, steps):
    p = tmp_path / "gate.yaml"
    lines = ["steps:"]
    for name, code, required in steps:
        lines += [f"  - name: {name}",
                  f"    cmd: [{sys.executable}, -c, 'import sys; sys.exit({code})']",
                  f"    required: {'true' if required else 'false'}"]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def test_check_all_pass_is_ready(gate, monkeypatch, tmp_path, capsys):
    cfg = gate_yaml(tmp_path, [("a", 0, True), ("b", 0, True)])
    out = str(tmp_path / "r.md")
    with pytest.raises(SystemExit) as e:
        run(gate, monkeypatch, "check", "--config", cfg, "--out", out)
    assert e.value.code == 0
    md = open(out, encoding="utf-8").read()
    assert "READY (all required steps passed)" in md
    assert md.count("| PASS |") == 2


def test_check_required_fail_is_not_ready(gate, monkeypatch, tmp_path):
    cfg = gate_yaml(tmp_path, [("a", 0, True), ("b", 3, True)])
    out = str(tmp_path / "r.md")
    with pytest.raises(SystemExit) as e:
        run(gate, monkeypatch, "check", "--config", cfg, "--out", out)
    assert e.value.code == 1
    md = open(out, encoding="utf-8").read()
    assert "NOT READY" in md and "**FAIL**" in md and "| 3 |" in md


def test_check_optional_fail_still_ready(gate, monkeypatch, tmp_path):
    cfg = gate_yaml(tmp_path, [("a", 0, True), ("opt", 2, False)])
    out = str(tmp_path / "r.md")
    with pytest.raises(SystemExit) as e:
        run(gate, monkeypatch, "check", "--config", cfg, "--out", out)
    assert e.value.code == 0
    md = open(out, encoding="utf-8").read()
    assert "READY" in md and "**FAIL**" in md          # failure shown, not fatal


def test_freeze_and_verify_roundtrip(gate, monkeypatch, tmp_path, capsys):
    (tmp_path / "t1.tex").write_text("a & 3.48", encoding="utf-8")
    (tmp_path / "r.json").write_text(json.dumps({"mape": 3.48}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    run(gate, monkeypatch, "freeze", "--files", "*.tex", "*.json",
        "--label", "test-v1", "--out", "fr.json")
    snap = json.loads((tmp_path / "fr.json").read_text(encoding="utf-8"))
    assert snap["label"] == "test-v1" and len(snap["files"]) == 2
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        run(gate, monkeypatch, "verify-freeze", "--against", "fr.json")
    assert e.value.code == 0

    (tmp_path / "t1.tex").write_text("a & 3.62", encoding="utf-8")  # numbers moved on
    (tmp_path / "r.json").unlink()
    with pytest.raises(SystemExit) as e:
        run(gate, monkeypatch, "verify-freeze", "--against", "fr.json")
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "CHANGED since freeze" in out and "MISSING" in out


def test_freeze_no_match_errors(gate, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as e:
        run(gate, monkeypatch, "freeze", "--files", "nothing*.xyz")
    assert "no files matched" in str(e.value.code)

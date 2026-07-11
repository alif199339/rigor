"""Offline tests for colab_run.py: parameter injection, dispatch staging, poll/collect."""
import json
import sys

import pytest


def _nb(with_persist=True):
    cells = [
        {"cell_type": "markdown", "metadata": {}, "source": ["# demo"]},
        {"cell_type": "code", "metadata": {"tags": ["parameters"]},
         "execution_count": None, "outputs": [],
         "source": ["SEED = 1\n", "SMOKE_TEST = True\n"]},
        {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
         "source": ["json.dump(results, open('results.json','w'))\n"]},
    ]
    if with_persist:
        cells.append({"cell_type": "code", "metadata": {}, "execution_count": None,
                      "outputs": [], "source": ["from google.colab import drive\n"]})
    return {"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": 5}


def test_inject_after_parameters_cell(colab):
    nb = colab.inject_parameters(_nb(), {"SEED": "42", "SMOKE_TEST": "False"})
    tags = [c.get("metadata", {}).get("tags") for c in nb["cells"]]
    assert tags[1] == ["parameters"] and tags[2] == ["injected-parameters"]
    src = "".join(nb["cells"][2]["source"])
    assert "SEED = 42" in src and "SMOKE_TEST = False" in src


def test_inject_requires_parameters_cell(colab):
    nb = {"cells": [{"cell_type": "code", "metadata": {}, "source": ["x=1\n"]}]}
    with pytest.raises(SystemExit):
        colab.inject_parameters(nb, {"SEED": "42"})


def test_dispatch_stages_and_injects_run_dir(colab, tmp_path, capsys, monkeypatch):
    nb_path = tmp_path / "nb.ipynb"
    nb_path.write_text(json.dumps(_nb()), encoding="utf-8")
    sync = tmp_path / "MyDrive" / "rigor-runs"
    monkeypatch.setattr(sys, "argv", [
        "colab_run.py", "dispatch", "--notebook", str(nb_path),
        "--sync-dir", str(sync), "--run-id", "exp1-s42",
        "--param", "SEED=42", "--param", "SMOKE_TEST=False"])
    colab.main()
    staged = json.loads((sync / "exp1-s42" / "notebook.ipynb").read_text(encoding="utf-8"))
    inj = "".join(staged["cells"][2]["source"])
    assert "SEED = 42" in inj
    assert "RIGOR_RUN_DIR = '/content/drive/MyDrive/rigor-runs/exp1-s42'" in inj
    out = capsys.readouterr().out
    assert "Run all" in out and "GPU approval" in out


def test_dispatch_warns_on_missing_persist_cell(colab, tmp_path, capsys, monkeypatch):
    nb_path = tmp_path / "nb.ipynb"
    nb_path.write_text(json.dumps(_nb(with_persist=False)), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "colab_run.py", "dispatch", "--notebook", str(nb_path),
        "--sync-dir", str(tmp_path / "s"), "--run-id", "r1"])
    colab.main()
    assert "will NOT sync back" in capsys.readouterr().out


def test_collect_validates_and_journals(colab, tmp_path, capsys, monkeypatch):
    run = tmp_path / "sync" / "r1"
    run.mkdir(parents=True)
    (run / "results.json").write_text(json.dumps({
        "smoke_test": False,
        "results": {"A": {"mape": 3.456}, "B": {"mape": 4.216}}}), encoding="utf-8")
    journal = tmp_path / "experiments.md"
    monkeypatch.setattr(sys, "argv", [
        "colab_run.py", "collect", "--sync-dir", str(tmp_path / "sync"),
        "--run-id", "r1", "--journal", str(journal)])
    colab.main()
    out = capsys.readouterr().out
    assert "A=3.456" in out and "verify-run" in out
    rows = journal.read_text(encoding="utf-8")
    assert "| r1 | colab |" in rows and "complete" in rows


def test_collect_fails_cleanly_when_absent(colab, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "colab_run.py", "collect", "--sync-dir", str(tmp_path), "--run-id", "nope",
        "--journal", str(tmp_path / "j.md")])
    with pytest.raises(SystemExit):
        colab.main()


def test_smoke_flag_surfaces_in_summary(colab):
    s = colab.summarize_results({"smoke_test": True, "results": {"A": {"mape": 9.9}}})
    assert "[SMOKE]" in s

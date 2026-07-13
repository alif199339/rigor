"""Offline tests for data_audit.py: CSV fingerprints, degeneracy detection
(the all-constant-column class), and drift classification."""
import json

import pytest


def write_csv(path, header, rows):
    path.write_text("\n".join([",".join(header)] + [",".join(map(str, r))
                                                    for r in rows]) + "\n",
                    encoding="utf-8")


GOOD_ROWS = [[1, 10.5, "a", 0], [2, 11.0, "b", 1], [3, 9.8, "c", 2]]


def test_fingerprint_csv_stats(dataa, tmp_path):
    p = tmp_path / "d.csv"
    write_csv(p, ["id", "load", "zone", "flag"], GOOD_ROWS)
    fp = dataa.fingerprint_file(str(p))
    assert fp["format"] == "csv" and fp["rows"] == 3
    assert fp["columns"] == ["id", "load", "zone", "flag"]
    st = fp["column_stats"]["load"]
    assert st["min"] == 9.8 and st["max"] == 11.0 and st["nulls"] == 0
    assert st["distinct"] == 3


def test_degeneracy_constant_and_allnull_and_dup(dataa, tmp_path):
    # the Holiday_cat class: a column that is constant over every row
    p = tmp_path / "d.csv"
    write_csv(p, ["x", "holiday_cat", "empty", "x_copy"],
              [[1, 0, "", 1], [2, 0, "", 2], [3, 0, "", 3]])
    fp = dataa.fingerprint_file(str(p))
    warns = dataa.degeneracies(fp)
    assert any("'holiday_cat' is CONSTANT" in w for w in warns)
    assert any("'empty' is ALL-NULL" in w for w in warns)
    assert any("DUPLICATES" in w and "x_copy" in w for w in warns)


def test_high_null_share_warns(dataa, tmp_path):
    p = tmp_path / "d.csv"
    write_csv(p, ["a", "b"], [[1, ""], [2, ""], [3, ""], [4, 5]])
    warns = dataa.degeneracies(dataa.fingerprint_file(str(p)))
    assert any("'b' is 75% null" in w for w in warns)


def test_drift_hard_vs_soft(dataa, tmp_path):
    p = tmp_path / "d.csv"
    write_csv(p, ["id", "load"], [[1, 10.5], [2, 11.0]])
    old = dataa.fingerprint_file(str(p))
    # same schema, one value changed -> soft (hash) only
    write_csv(p, ["id", "load"], [[1, 10.5], [2, 11.1]])
    hard, soft = dataa.drift(old, dataa.fingerprint_file(str(p)))
    assert not hard and any("hash changed" in s for s in soft)
    # row added AND a new null -> hard
    write_csv(p, ["id", "load"], [[1, 10.5], [2, ""], [3, 12.0]])
    hard, _ = dataa.drift(old, dataa.fingerprint_file(str(p)))
    assert any("rows: 2 -> 3" in h for h in hard)
    assert any("'load' nulls: 0 -> 1" in h for h in hard)
    # column removed -> hard
    write_csv(p, ["id"], [[1], [2]])
    hard, _ = dataa.drift(old, dataa.fingerprint_file(str(p)))
    assert any("columns changed" in h and "load" in h for h in hard)


def test_drift_collapse_to_constant_is_hard(dataa, tmp_path):
    p = tmp_path / "d.csv"
    write_csv(p, ["hol"], [[0], [1], [2]])
    old = dataa.fingerprint_file(str(p))
    write_csv(p, ["hol"], [[0], [0], [0]])           # the Holiday_cat regression
    hard, _ = dataa.drift(old, dataa.fingerprint_file(str(p)))
    assert any("COLLAPSED to constant" in h for h in hard)


def test_cmd_verify_exit_codes(dataa, tmp_path, monkeypatch, capsys):
    import argparse
    d = tmp_path / "bundle"
    d.mkdir()
    write_csv(d / "a.csv", ["x", "y"], [[1, 2], [3, 4]])
    args = argparse.Namespace(path=str(d), out=None, recursive=False, strict=True)
    with pytest.raises(SystemExit) as e:
        dataa.cmd_fingerprint(args)
    assert e.value.code == 0                          # clean data, even under --strict
    # identical data verifies clean
    vargs = argparse.Namespace(path=str(d), against=str(d / "fingerprint.json"),
                               recursive=False)
    with pytest.raises(SystemExit) as e:
        dataa.cmd_verify(vargs)
    assert e.value.code == 0
    # break it: y goes constant
    write_csv(d / "a.csv", ["x", "y"], [[1, 9], [3, 9]])
    with pytest.raises(SystemExit) as e:
        dataa.cmd_verify(vargs)
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "COLLAPSED to constant" in out or "CONSTANT" in out


def test_strict_fingerprint_exits_2_on_born_broken(dataa, tmp_path):
    import argparse
    d = tmp_path / "bundle"
    d.mkdir()
    write_csv(d / "a.csv", ["k"], [[7], [7], [7]])     # constant from birth
    args = argparse.Namespace(path=str(d), out=None, recursive=False, strict=True)
    with pytest.raises(SystemExit) as e:
        dataa.cmd_fingerprint(args)
    assert e.value.code == 2

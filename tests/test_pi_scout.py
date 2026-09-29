"""Offline tests for pi_scout.py: the transparent score, provenance labels, and the
seed -> harvest -> authors -> shortlist -> status pipeline with the lit-review HTTP
client stubbed (no network, no sleeping)."""
import argparse
import datetime
import json

import pytest

CUR = datetime.date.today().year


@pytest.fixture(autouse=True)
def _no_sleep(piscout, monkeypatch):
    monkeypatch.setattr(piscout.time, "sleep", lambda s: None)


def ns(out, **kw):
    return argparse.Namespace(out=str(out), **kw)


def test_h_points_bands(piscout):
    assert [piscout._h_points(h) for h in (None, 3, 8, 15, 25, 40, 90)] == \
        [0, 0, 4, 6, 8, 10, 10]


def test_seed_author_ids_skips_anchor_seeds(piscout):
    seeds = {"MINE": {"authors": [{"authorId": "me"}, {"authorId": "coauthor"}]},
             "ANCH": {"_anchor": True, "authors": [{"authorId": "famous"}]}}
    assert piscout._seed_author_ids(seeds) == {"me", "coauthor"}


def test_pretty_source_labels(piscout):
    seeds = {"MINE": {"title": "My paper"}, "ANCH": {"title": "Canonical", "_anchor": True}}
    assert piscout._pretty_source("cites:MINE", seeds) == "cites-you(My paper)"
    assert piscout._pretty_source("cites:ANCH", seeds) == "cites-anchor(Canonical)"
    assert piscout._pretty_source("recs:MINE", seeds) == "related-to(My paper)"
    assert piscout._pretty_source("search:graph load", seeds) == "search:graph load"


def test_filter_year_drops_undated_and_old(piscout):
    ps = [{"year": CUR}, {"year": CUR - 5}, {"year": None}, None]
    assert piscout._filter_year(ps, CUR - 2) == [{"year": CUR}]


def test_score_components_and_flags(piscout):
    ev = [{"paperId": f"p{i}", "year": CUR, "last": True, "first": False,
           "sources": ["cites:MINE"]} for i in range(5)]
    total, comp, flags, stats = piscout._score(ev, {"hIndex": 60, "paperCount": 400})
    assert comp == {"warm": 36, "senior": 18, "breadth": 15, "eminence": 10, "recency": 5}
    assert total == 84 and "superstar" in flags and stats["n_warm"] == 5
    _, _, flags, _ = piscout._score(
        [{"paperId": "x", "year": CUR - 5, "last": False, "first": True, "sources": []}],
        {"hIndex": 2, "paperCount": 4})
    assert "likely-student" in flags
    _, _, flags, _ = piscout._score(ev, {"_missing": True})
    assert "no-metrics" in flags


def test_pipeline_offline(piscout, lit, tmp_path, monkeypatch):
    out = tmp_path / "pi-scout"
    me = {"authorId": "me", "name": "Me"}
    co = {"authorId": "co", "name": "Coauthor"}
    warm_pi = {"authorId": "pi1", "name": "Warm PI"}
    cold_pi = {"authorId": "pi2", "name": "Cold PI"}
    student = {"authorId": "st", "name": "Student"}

    def fake_get(url):
        if "/paper/DOI" in url:
            return {"paperId": "MINE", "title": "My paper", "year": CUR - 1,
                    "authors": [me, co], "externalIds": {"DOI": "10.1/mine"}}
        if "/citations" in url:
            return {"next": None, "data": [
                {"citingPaper": {"paperId": "C1", "title": "Cites me", "year": CUR,
                                 "authors": [student, co, warm_pi]}},
                {"citingPaper": {"paperId": "OLD", "title": "Old citer", "year": CUR - 9,
                                 "authors": [cold_pi]}}]}
        if "/recommendations/" in url:
            return {"recommendedPapers": [
                {"paperId": "R1", "title": "Related work", "year": CUR,
                 "authors": [student, cold_pi]}]}
        if "/paper/search" in url:
            return {"data": None}                       # S2's literal-null answer
        raise AssertionError(f"unexpected URL {url}")

    def fake_post(url, payload):
        recs = {"pi1": {"authorId": "pi1", "name": "Warm PI", "hIndex": 30,
                        "paperCount": 120, "citationCount": 4000, "affiliations": ["U"]},
                "pi2": {"authorId": "pi2", "name": "Cold PI", "hIndex": 45,
                        "paperCount": 300, "citationCount": 9000},
                "st": {"authorId": "st", "name": "Student", "hIndex": 2, "paperCount": 5}}
        return [recs.get(i) for i in payload["ids"]]

    monkeypatch.setattr(lit, "http_get", fake_get)
    monkeypatch.setattr(lit, "http_post", fake_post)

    piscout.cmd_seed(ns(out, id="DOI:10.1/mine", title=None, anchor=False))
    piscout.cmd_harvest(ns(out, year_from=None, limit_cites=100, limit_recs=30,
                           query=["graph load"], limit_search=25))
    store = json.loads((out / "papers.json").read_text(encoding="utf-8"))
    assert set(store) == {"C1", "R1"}                    # the 9-year-old citer is dropped
    assert store["C1"]["_sources"] == ["cites:MINE"]

    piscout.cmd_authors(ns(out, keep_coauthors=False, refresh=False))
    authors = json.loads((out / "authors.json").read_text(encoding="utf-8"))
    assert "co" not in authors and "me" not in authors  # seed coauthors excluded
    assert authors["pi1"]["hIndex"] == 30 and authors["pi1"]["_fetched"]

    piscout.cmd_shortlist(ns(out, top=10, min_h=0, keep_coauthors=False))
    cands = json.loads((out / "candidates.json").read_text(encoding="utf-8"))
    assert cands[0]["authorId"] == "pi1"                 # the warm lead ranks first
    assert cands[0]["components"]["warm"] == 12
    assert "likely-student" in next(c for c in cands if c["authorId"] == "st")["flags"]
    md = (out / "candidates.md").read_text(encoding="utf-8")
    assert "NOT the deliverable" in md and "cites-you(My paper)" in md


def test_status_suggests_next_step(piscout, tmp_path, capsys):
    piscout.cmd_status(ns(tmp_path / "empty"))
    assert "next step -> seed" in capsys.readouterr().out


def test_harvest_requires_seeds_or_query(piscout, tmp_path):
    with pytest.raises(SystemExit):
        piscout.cmd_harvest(ns(tmp_path, year_from=None, limit_cites=10, limit_recs=5,
                               query=[], limit_search=5))

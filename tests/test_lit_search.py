"""Offline tests for lit_search.py's store, provenance, and pure helpers."""


def test_merge_dedupes_and_tags_provenance(lit):
    store = {}
    p1 = {"paperId": "X1", "title": "A paper", "year": 2020}
    n = lit.merge(store, [p1], "search:q1")
    assert n == 1 and store["X1"]["_sources"] == ["search:q1"]
    # same paper from a second query: no new entry, provenance appended
    n = lit.merge(store, [dict(p1)], "search:q2")
    assert n == 0 and store["X1"]["_sources"] == ["search:q1", "search:q2"]
    # duplicate source is not re-appended
    lit.merge(store, [dict(p1)], "search:q2")
    assert store["X1"]["_sources"].count("search:q2") == 1


def test_merge_fills_missing_fields_without_overwriting(lit):
    store = {}
    lit.merge(store, [{"paperId": "X1", "title": "A paper", "abstract": None}], "s1")
    lit.merge(store, [{"paperId": "X1", "title": "SHOULD NOT REPLACE",
                       "abstract": "now present"}], "s2")
    assert store["X1"]["abstract"] == "now present"      # hole filled
    assert store["X1"]["title"] == "A paper"             # existing value kept


def test_merge_skips_null_papers(lit):
    store = {}
    assert lit.merge(store, [None, {}, {"title": "no id"}], "s") == 0


def test_reconstruct_abstract_from_inverted_index(lit):
    inv = {"world.": [1], "Hello": [0], "again": [3], "hello": [2]}
    assert lit.reconstruct_abstract(inv) == "Hello world. hello again"
    assert lit.reconstruct_abstract(None) is None
    assert lit.reconstruct_abstract({}) is None


def test_pdf_basename_is_deterministic_and_safe(lit):
    p = {"paperId": "abc", "title": "Graph WaveNet: for Deep* Modeling?", "year": 2019}
    b = lit._pdf_basename(p)
    assert b.startswith("2019_") and "*" not in b and "?" not in b and ":" not in b
    assert b == lit._pdf_basename(p)  # stable


def test_score_weights_influential_citations(lit):
    a = {"citationCount": 10, "influentialCitationCount": 0}
    b = {"citationCount": 1, "influentialCitationCount": 4}
    assert lit._score(b) > lit._score(a)


def test_search_merges_into_store(lit, tmp_path, monkeypatch):
    out = str(tmp_path / "topic")
    monkeypatch.setattr(lit, "http_get", lambda url: {"data": [
        {"paperId": "P1", "title": "T1", "year": 2024},
        {"paperId": "P2", "title": "T2", "year": 2023},
    ]})
    monkeypatch.setattr(lit.time, "sleep", lambda s: None)
    import argparse
    args = argparse.Namespace(out=out, query="anything", limit=25, year_from=None,
                              bulk=False, max=300)
    lit.cmd_search(args)
    store = lit.load_store(out)
    assert set(store) == {"P1", "P2"}
    assert store["P1"]["_sources"] == ["search:anything"]

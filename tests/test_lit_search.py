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


def test_tokens_strip_stopwords_and_short_words(lit):
    toks = lit._tokens("The novel graph-based approach for load forecasting in a grid")
    assert "load" in toks and "forecasting" in toks and "grid" in toks
    assert "the" not in toks and "for" not in toks and "novel" not in toks


def test_relevance_title_outranks_abstract(lit):
    papers = [
        {"paperId": "T", "title": "Graph load forecasting",
         "abstract": "unrelated words entirely"},
        {"paperId": "A", "title": "Something else entirely",
         "abstract": "graph load forecasting appears only here"},
        {"paperId": "N", "title": "Irrelevant", "abstract": "nothing shared"},
    ]
    rel, matched = lit._relevance(papers, "graph load forecasting")
    assert rel[0] == 1.0                       # title match normalizes to top
    assert 0 < rel[1] < rel[0]                 # abstract-only match ranks below
    assert rel[2] == 0.0
    assert "graph" in matched[0]


def test_relevance_rejects_empty_focus(lit):
    import pytest
    with pytest.raises(SystemExit):
        lit._relevance([{"paperId": "x", "title": "t"}], "the and of")


def test_abstract_of_provenance_ladder(lit):
    native = {"abstract": "native text"}
    oa = {"abstract": "", "_abstract_openalex": "openalex text"}
    tl = {"tldr": {"text": "tldr text"}}
    none = {"title": "no abstract anywhere"}
    assert lit._abstract_of(native) == ("native text", None)
    assert lit._abstract_of(oa)[1] == "abstract via OpenAlex"
    assert lit._abstract_of(tl)[1] == "TL;DR (Semantic Scholar)"
    assert lit._abstract_of(none) == (None, None)
    long = {"abstract": "word " * 500}
    text, _ = lit._abstract_of(long)
    assert len(text) < 700 and text.endswith("…")


def test_report_with_focus_tiers_and_rich_entries(lit, tmp_path):
    import argparse, json, os
    out = str(tmp_path / "topic")
    os.makedirs(out)
    store = {
        "P1": {"paperId": "P1", "title": "Graph load forecasting for power grids",
               "year": 2024, "abstract": "We forecast electricity load with graphs.",
               "citationCount": 5, "externalIds": {"DOI": "10.1/p1"},
               "_sources": ["search:q"]},
        "P2": {"paperId": "P2", "title": "A totally unrelated biology paper",
               "year": 2020, "citationCount": 900, "_sources": ["snowball-references:x"]},
    }
    json.dump(store, open(os.path.join(out, "papers.json"), "w", encoding="utf-8"))
    args = argparse.Namespace(out=out, focus="graph load forecasting power grid",
                              focus_file=None)
    lit.cmd_report(args)
    md = open(os.path.join(out, "papers.md"), encoding="utf-8").read()
    assert "## Core" in md and "## Peripheral" in md
    assert "[DOI:10.1/p1](https://doi.org/10.1/p1)" in md          # id link present
    assert "We forecast electricity load with graphs." in md       # abstract present
    assert "(abstract unavailable" in md                           # P2's honest gap
    assert "**Relevance:**" in md and "matches:" in md
    # the relevant paper must appear under Core, before the highly-cited irrelevant one
    assert md.index("Graph load forecasting for power grids") < md.index("totally unrelated")


def test_report_without_focus_still_rich(lit, tmp_path):
    import argparse, json, os
    out = str(tmp_path / "t2")
    os.makedirs(out)
    store = {"P1": {"paperId": "P1", "title": "Only paper", "year": 2023,
                    "citationCount": 1, "_sources": ["search:q"]}}
    json.dump(store, open(os.path.join(out, "papers.json"), "w", encoding="utf-8"))
    lit.cmd_report(argparse.Namespace(out=out, focus=None, focus_file=None))
    md = open(os.path.join(out, "papers.md"), encoding="utf-8").read()
    assert "ranked by influence" in md and "### 1. Only paper (2023)" in md
    assert "Semantic Scholar](https://www.semanticscholar.org/paper/P1)" in md


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

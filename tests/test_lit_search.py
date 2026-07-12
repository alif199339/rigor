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


def test_snowball_survives_null_data(lit, tmp_path, monkeypatch):
    # regression: S2 returns {"data": null} -- a literal null value, not a missing
    # key -- for some papers' reference lists, which crashed `snowball --direction refs`
    import argparse
    out = str(tmp_path / "topic")
    monkeypatch.setattr(lit, "http_get", lambda url: {"data": None})
    monkeypatch.setattr(lit.time, "sleep", lambda s: None)
    lit.cmd_snowball(argparse.Namespace(out=out, seed="DOI:10.1/x",
                                        direction="refs", limit=50))   # must not raise
    assert lit.load_store(out) == {}


def test_search_and_lookup_survive_null_data(lit, tmp_path, monkeypatch):
    import argparse
    out = str(tmp_path / "topic")
    monkeypatch.setattr(lit, "http_get", lambda url: {"data": None})
    monkeypatch.setattr(lit.time, "sleep", lambda s: None)
    lit.cmd_search(argparse.Namespace(out=out, query="q", limit=25, year_from=None,
                                      bulk=False, max=300))            # must not raise
    lit.cmd_lookup(argparse.Namespace(out=out, id=None, title="Some title"))
    assert lit.load_store(out) == {}


def test_oa_filter_value_strips_reserved_chars(lit):
    v = lit._oa_filter_value("Attention, please: graphs, load | forecasting,")
    assert "," not in v and "|" not in v
    assert "graphs load forecasting" in v            # collapsed to single spaces
    assert lit._oa_filter_value(None) == ""


def test_enrich_title_fallback_sanitizes_filter(lit, tmp_path, monkeypatch):
    # regression: a title containing/ending in a comma reached OpenAlex's filter
    # syntax verbatim (commas separate filters, no escaping) -> HTTP 400 mid-run
    import argparse, json, os
    out = str(tmp_path / "topic")
    os.makedirs(out)
    store = {"P1": {"paperId": "P1", "title": "Forecasting loads, grids, and graphs,",
                    "_sources": ["search:q"]}}
    json.dump(store, open(os.path.join(out, "papers.json"), "w", encoding="utf-8"))
    seen = []
    monkeypatch.setattr(lit, "openalex_get",
                        lambda url: seen.append(url) or {"results": []})
    monkeypatch.setattr(lit.time, "sleep", lambda s: None)
    lit.cmd_enrich(argparse.Namespace(out=out, source="openalex"))
    assert seen, "title fallback was never queried"
    assert "," not in seen[-1] and "%2C" not in seen[-1]


def test_openalex_get_returns_none_on_400(lit, monkeypatch):
    # a malformed query for ONE record must not abort a checkpointed batch run
    import io
    import urllib.error
    import urllib.request

    def boom(req, timeout=60):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request",
                                     None, io.BytesIO(b""))
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert lit.openalex_get(lit.OPENALEX + "?filter=title.search:x") is None


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

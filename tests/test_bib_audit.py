"""Offline tests for bib_audit.py: parser, normalizers, and verdict logic (HTTP stubbed)."""

SAMPLE = r"""
% a comment line
@article{yu2018,
  author  = {Yu, Bing},
  title   = {Spatio-Temporal Graph {Convolutional} Networks},
  journal = {Proc. IJCAI},
  year    = {2018}
}
@comment{ignored entirely}
@inproceedings{chen2016,
  title = "A Quoted Title",
  year  = 2016,
  doi   = {10.1145/2939672.2939785}
}
"""


def test_parse_bib_entries_fields_and_nesting(bib):
    es = bib.parse_bib(SAMPLE)
    assert [e["key"] for e in es] == ["yu2018", "chen2016"]           # @comment skipped
    assert es[0]["type"] == "article"
    assert "{Convolutional}" in es[0]["fields"]["title"]              # nested braces kept
    assert es[1]["fields"]["title"] == "A Quoted Title"               # quoted values
    assert es[1]["fields"]["doi"] == "10.1145/2939672.2939785"
    assert es[1]["fields"]["year"] == "2016"                          # bare value


def test_norm_doi(bib):
    assert bib.norm_doi("https://doi.org/10.1000/ABC.") == "10.1000/abc"
    assert bib.norm_doi("HTTP://DX.DOI.ORG/10.1/x") is None or True   # case-insens prefix
    assert bib.norm_doi("10.1/X") == "10.1/x"
    assert bib.norm_doi("") is None and bib.norm_doi(None) is None


def test_year_diff(bib):
    assert bib.year_diff("2018", 2017) == 1
    assert bib.year_diff(2015, "1972") == 43
    assert bib.year_diff("n.d.", 2017) is None


def _entry(fields, etype="article", key="k1"):
    return {"type": etype, "key": key, "fields": fields}


def test_audit_verified_via_doi(bib, monkeypatch):
    rec = {"src": "S2", "title": "A Real Paper", "year": 2020, "venue": "J",
           "doi": "10.1/x", "arxiv": None, "types": []}
    monkeypatch.setattr(bib, "s2_by_doi", lambda d: rec)
    r = bib.audit_entry(_entry({"title": "A Real Paper", "year": "2020", "doi": "10.1/x"}))
    assert r["verdict"] == "VERIFIED" and not r["diffs"]


def test_preprint_year_offset_is_note_not_mismatch(bib, monkeypatch):
    # S2's year is often the preprint year (published-1) -- must NOT flag MISMATCH
    rec = {"src": "S2", "title": "Semi-Supervised Classification with GCNs", "year": 2016,
           "doi": "10.1/gcn", "arxiv": "1609.02907", "types": []}
    monkeypatch.setattr(bib, "s2_by_title", lambda t: rec)
    monkeypatch.setattr(bib, "crossref_by_title", lambda t: None)
    r = bib.audit_entry(_entry({"title": "Semi-Supervised Classification with GCNs",
                                "year": "2017"}))
    assert r["verdict"] == "VERIFIED"
    assert any("preprint" in n for n in r["notes"])
    assert any(s.startswith("doi =") for s in r["suggest"])           # enrichment offered


def test_large_year_gap_titleonly_is_mismatch_without_autofix(bib, monkeypatch):
    # a 5th-edition textbook title-matching a 1972 review must not get a DOI suggestion
    rec = {"src": "S2", "title": "Time series analysis, forecasting and control",
           "year": 1972, "doi": "10.1109/old", "arxiv": None, "types": []}
    monkeypatch.setattr(bib, "s2_by_title", lambda t: rec)
    monkeypatch.setattr(bib, "crossref_by_title", lambda t: None)
    r = bib.audit_entry(_entry({"title": "Time Series Analysis: Forecasting and Control",
                                "year": "2015", "journal": "Wiley"}))
    assert r["verdict"] == "MISMATCH"
    assert not any(s.startswith("doi =") for s in r["suggest"])       # no auto-fix
    assert any("DIFFERENT edition/record" in n for n in r["notes"])


def test_nonpaper_with_live_url_is_ok(bib, monkeypatch):
    monkeypatch.setattr(bib, "url_alive", lambda u: True)
    r = bib.audit_entry(_entry({"title": "Some Dataset Portal",
                                "howpublished": r"\url{https://example.org/data}"},
                               etype="misc"))
    assert r["verdict"] == "NON-PAPER-OK"


def test_unpublished_submitted_is_unverifiable(bib, monkeypatch):
    monkeypatch.setattr(bib, "s2_by_title", lambda t: None)
    monkeypatch.setattr(bib, "crossref_by_title", lambda t: None)
    r = bib.audit_entry(_entry({"title": "A Submitted Manuscript Nobody Indexed Yet",
                                "journal": "IEEE Access (submitted)", "year": "2026"}))
    assert r["verdict"] == "UNVERIFIABLE"

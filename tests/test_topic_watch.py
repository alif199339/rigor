"""Offline test of topic_watch: query recovery, new-vs-known diff, report, --merge."""
import datetime
import json
import sys


def test_watch_diffs_and_merges(watch, lit, tmp_path, monkeypatch):
    out = tmp_path / "topic"
    out.mkdir()
    store = {"KNOWN1": {"paperId": "KNOWN1", "title": "Known paper", "year": 2024,
                        "_sources": ["search:graph load forecasting"]}}
    (out / "papers.json").write_text(json.dumps(store), encoding="utf-8")

    monkeypatch.setattr(lit, "http_get", lambda url: {"data": [
        {"paperId": "KNOWN1", "title": "Known paper", "year": 2024,
         "authors": [{"name": "A B"}]},
        {"paperId": "NEW999", "title": "A brand new forecasting paper", "year": 2026,
         "authors": [{"name": "New Author"}], "venue": "Test J", "citationCount": 3,
         "externalIds": {"DOI": "10.9999/new"}, "tldr": {"text": "A novel result."}},
    ]})
    monkeypatch.setattr(lit.time, "sleep", lambda s: None)
    monkeypatch.setattr(sys, "argv", ["topic_watch.py", "--out", str(out),
                                      "--since-year", "2025", "--limit", "10", "--merge"])
    watch.main()

    today = datetime.date.today().isoformat()
    md = (out / f"watch_{today}.md").read_text(encoding="utf-8")
    assert "A brand new forecasting paper" in md
    assert "10.9999/new" in md
    assert "Known paper" not in md.split("collection")[-1]      # known one not re-listed

    store2 = json.loads((out / "papers.json").read_text(encoding="utf-8"))
    assert "NEW999" in store2
    assert any(s.startswith("watch:") for s in store2["NEW999"]["_sources"])


def test_watch_survives_null_data(watch, lit, tmp_path, monkeypatch):
    # regression: S2 can answer {"data": null}; the scan loop must treat it as empty
    out = tmp_path / "topic"
    out.mkdir()
    store = {"K": {"paperId": "K", "title": "Known", "year": 2024,
                   "_sources": ["search:some query"]}}
    (out / "papers.json").write_text(json.dumps(store), encoding="utf-8")
    monkeypatch.setattr(lit, "http_get", lambda url: {"data": None})
    monkeypatch.setattr(lit.time, "sleep", lambda s: None)
    monkeypatch.setattr(sys, "argv", ["topic_watch.py", "--out", str(out),
                                      "--since-year", "2025", "--limit", "10"])
    watch.main()                                                    # must not raise
    today = datetime.date.today().isoformat()
    md = (out / f"watch_{today}.md").read_text(encoding="utf-8")
    assert "0 new paper(s)" in md


def test_recover_queries_reads_search_and_bulk_tags(watch):
    store = {"a": {"_sources": ["search:q one", "snowball-references:X"]},
             "b": {"_sources": ["bulk:q two"]},
             "c": {"_sources": ["recommend:Y"]}}
    q = watch.recover_queries(store)
    assert q == {"q one": "search", "q two": "bulk"}

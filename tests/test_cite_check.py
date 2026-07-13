"""Offline tests for cite_check.py: sentence/cite extraction, bib parsing,
store matching, worksheet statuses. No network anywhere."""
import argparse
import json

TEX = r"""
\begin{document}
% \cite{commented} must not count
Graph models beat statistical baselines on short horizons~\citep{yu2018stgcn}.
Prior work combined two ideas \cite{wu2019gwn, li2018dcrnn}. Unrelated sentence.
As \citet{kipf2017} showed, spectral filters simplify.
\end{document}
"""

BIB = r"""
@inproceedings{yu2018stgcn,
  title = {Spatio-Temporal {Graph} Convolutional Networks},
  doi   = {10.24963/ijcai.2018/505},
  year  = {2018}
}
@inproceedings{wu2019gwn,
  title = "Graph WaveNet for Deep Spatial-Temporal Graph Modeling",
  year  = 2019
}
@article{kipf2017,
  title = {Semi-Supervised Classification with Graph Convolutional Networks},
  year  = {2017}
}
"""


def test_sentences_with_cites_extraction(citec):
    pairs = citec.sentences_with_cites(TEX.replace(r"% \cite{commented} must not count", ""))
    flat = {k for _s, keys in pairs for k in keys}
    assert flat == {"yu2018stgcn", "wu2019gwn", "li2018dcrnn", "kipf2017"}
    multi = next(keys for s, keys in pairs if "two ideas" in s)
    assert multi == ["wu2019gwn", "li2018dcrnn"]              # multi-key split
    assert all("[CITE]" in s for s, _k in pairs)              # cite cmd replaced
    assert not any("Unrelated sentence" in s for s, _k in pairs)


def test_comments_stripped_before_extraction(citec, tmp_path):
    p = tmp_path / "m.tex"
    p.write_text(TEX, encoding="utf-8")
    body = citec.load_tex_body(str(p))
    assert "commented" not in body


def test_parse_bib_keys_titles_and_doi(citec, tmp_path):
    p = tmp_path / "r.bib"
    p.write_text(BIB, encoding="utf-8")
    bib = citec.parse_bib_keys(str(p))
    assert set(bib) == {"yu2018stgcn", "wu2019gwn", "kipf2017"}
    assert bib["yu2018stgcn"]["doi"] == "10.24963/ijcai.2018/505"
    assert "Graph" in bib["yu2018stgcn"]["title"]             # nested braces survive
    assert bib["wu2019gwn"]["title"].startswith("Graph WaveNet")   # quoted values


def test_match_paper_by_doi_then_title(citec):
    papers = [
        {"paperId": "P1", "title": "Spatio-Temporal Graph Convolutional Networks",
         "externalIds": {"DOI": "10.24963/ijcai.2018/505"}, "abstract": "We propose..."},
        {"paperId": "P2", "title": "Something Entirely Different", "abstract": "x"},
    ]
    by_doi = {"10.24963/ijcai.2018/505": papers[0]}
    hit, how = citec.match_paper({"doi": "https://doi.org/10.24963/IJCAI.2018/505",
                                  "title": "wrong title on purpose"}, by_doi, papers)
    assert hit["paperId"] == "P1" and how == "DOI"            # DOI outranks title
    hit2, how2 = citec.match_paper(
        {"doi": None, "title": "Spatio-Temporal Graph Convolutional Networks"},
        {}, papers)
    assert hit2["paperId"] == "P1" and how2.startswith("title")
    miss, _ = citec.match_paper({"doi": None, "title": "Totally Unrelated Work"},
                                {}, papers)
    assert miss is None


def test_build_statuses_end_to_end(citec, tmp_path, capsys):
    (tmp_path / "m.tex").write_text(TEX, encoding="utf-8")
    (tmp_path / "r.bib").write_text(BIB, encoding="utf-8")
    store = {"P1": {"paperId": "P1",
                    "title": "Spatio-Temporal Graph Convolutional Networks",
                    "externalIds": {"DOI": "10.24963/ijcai.2018/505"},
                    "abstract": "We propose STGCN for traffic forecasting."},
             "P3": {"paperId": "P3",
                    "title": "Semi-Supervised Classification with Graph "
                             "Convolutional Networks",
                    "externalIds": {}}}                        # in store, no abstract
    (tmp_path / "papers.json").write_text(json.dumps(store), encoding="utf-8")
    args = argparse.Namespace(tex=str(tmp_path / "m.tex"), bib=str(tmp_path / "r.bib"),
                              papers=[str(tmp_path / "papers.json")],
                              out_dir=str(tmp_path))
    rows, missing = citec.build(args)
    st = {r["key"]: r["status"] for r in rows}
    assert st["yu2018stgcn"] == "PAIRED"
    assert st["kipf2017"] == "NO-ABSTRACT"
    assert st["wu2019gwn"] == "NOT-IN-STORE"
    assert st["li2018dcrnn"] == "NO-BIB-ENTRY" and missing == {"li2018dcrnn"}
    citec.write_worksheet(rows, missing, args)
    md = (tmp_path / "cite_check_worksheet.md").read_text(encoding="utf-8")
    assert "We propose STGCN" in md and "NO-BIB-ENTRY" in md
    assert "missing from the .bib" in capsys.readouterr().err

"""cite_check.py -- pair every citation-bearing claim sentence with the cited work.

RIGOR skill: /cite-check. Stdlib-only, Python 3.10+, fully offline.

bib-audit verifies a cited work EXISTS; this tool sets up verifying that it SAYS
what the manuscript claims it says (miscitation -- the most common real-world
citation failure). The script does the mechanical half: extract every sentence
containing a \\cite-family command, resolve each key against the .bib, and attach
the best available abstract/summary from one or more lit-review stores
(papers.json). The semantic half -- judging SUPPORTED / NOT-SUPPORTED /
CANT-VERIFY -- is agent work under the SKILL.md rules, on the worksheet this
script emits. The script never judges and never edits the manuscript.

Run:
  python cite_check.py --tex main.tex --bib refs.bib \
      --papers "literature/*/papers.json" --out-dir .

Outputs cite_check_worksheet.md (+ .json). Exit 1 if any cited key is missing
from the .bib (a hard, mechanical error); 0 otherwise.
"""
import argparse
import difflib
import glob
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CITE_CMDS = r"(?:cite|citep|citet|citealp|citeauthor|parencite|textcite|autocite)"
CITE_RE = re.compile(r"\\" + CITE_CMDS + r"\*?(?:\[[^\]]*\])*\{([^}]+)\}")


# ---------------- manuscript side ----------------

def load_tex_body(path):
    text = open(path, encoding="utf-8").read()
    m = re.search(r"\\begin\{document\}(.*)\\end\{document\}", text, re.S)
    text = m.group(1) if m else text
    return re.sub(r"(?<!\\)%.*", "", text)               # strip comments


def sentences_with_cites(body):
    """(sentence, [keys]) for every sentence containing a cite command."""
    out = []
    for para in re.split(r"\n\s*\n", body):
        para = re.sub(r"\s+", " ", para).strip()
        if "\\cite" not in para and "cite{" not in para:
            continue
        # split on sentence enders not inside a brace group (good enough for prose)
        for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z\\`\"'([])", para):
            keys = []
            for m in CITE_RE.finditer(sent):
                keys += [k.strip() for k in m.group(1).split(",") if k.strip()]
            if keys:
                clean = CITE_RE.sub("[CITE]", sent).strip()
                out.append((clean, keys))
    return out


# ---------------- bibliography side (minimal, self-contained) ----------------

def parse_bib_keys(path):
    """key -> {title, doi} from a .bib file (brace-balanced field scan)."""
    text = open(path, encoding="utf-8").read()
    entries = {}
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        if m.group(1).lower() in ("comment", "string", "preamble"):
            continue
        # scan to the matching close brace of this entry
        i, depth = m.end(), 1
        start = i
        while i < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        fields = text[start:i - 1]
        def field(name):
            fm = re.search(name + r"\s*=\s*[{\"]", fields, re.I)
            if not fm:
                return None
            j, d = fm.end(), 1
            closer = "}" if fields[fm.end() - 1] == "{" else '"'
            k = j
            while k < len(fields):
                if closer == "}":
                    d += {"{": 1, "}": -1}.get(fields[k], 0)
                    if d == 0:
                        break
                elif fields[k] == '"':
                    break
                k += 1
            return re.sub(r"\s+", " ", fields[j:k]).strip("{} ")
        entries[m.group(2)] = {"title": field("title"), "doi": field("doi")}
    return entries


# ---------------- abstract side (lit-review stores) ----------------

def norm_doi(d):
    if not d:
        return None
    d = d.strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    return d.rstrip(".") or None


def norm_title(t):
    return re.sub(r"[^a-z0-9 ]", "", (t or "").lower()).strip()


def load_paper_pool(papers_globs):
    """All papers from every matching papers.json, indexed by DOI + kept as a list."""
    by_doi, all_papers = {}, []
    for g in papers_globs:
        for p in sorted(glob.glob(g)):
            try:
                store = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            for paper in store.values():
                all_papers.append(paper)
                doi = norm_doi((paper.get("externalIds") or {}).get("DOI"))
                if doi:
                    by_doi[doi] = paper
    return by_doi, all_papers


def abstract_of(paper):
    if (paper.get("abstract") or "").strip():
        return paper["abstract"].strip(), "S2 abstract"
    if (paper.get("_abstract_openalex") or "").strip():
        return paper["_abstract_openalex"].strip(), "abstract via OpenAlex"
    tl = (paper.get("tldr") or {}).get("text")
    if tl:
        return tl.strip(), "TL;DR (Semantic Scholar)"
    return None, None


def match_paper(bib_entry, by_doi, all_papers):
    doi = norm_doi(bib_entry.get("doi"))
    if doi and doi in by_doi:
        return by_doi[doi], "DOI"
    bt = norm_title(bib_entry.get("title"))
    if not bt:
        return None, None
    best, best_r = None, 0.0
    for paper in all_papers:
        r = difflib.SequenceMatcher(None, bt, norm_title(paper.get("title"))).ratio()
        if r > best_r:
            best, best_r = paper, r
    return (best, f"title {best_r:.2f}") if best_r >= 0.85 else (None, None)


# ---------------- worksheet ----------------

def build(args):
    body = load_tex_body(args.tex)
    pairs = sentences_with_cites(body)
    bib = parse_bib_keys(args.bib)
    by_doi, all_papers = load_paper_pool(args.papers or [])

    rows, missing = [], set()
    for sent, keys in pairs:
        for key in keys:
            row = {"key": key, "sentence": sent, "status": None,
                   "title": None, "abstract": None, "abstract_src": None,
                   "matched_by": None}
            ent = bib.get(key)
            if ent is None:
                row["status"] = "NO-BIB-ENTRY"
                missing.add(key)
            else:
                row["title"] = ent.get("title")
                paper, how = match_paper(ent, by_doi, all_papers)
                if paper:
                    abs_text, src = abstract_of(paper)
                    if abs_text:
                        row.update(status="PAIRED", abstract=abs_text,
                                   abstract_src=src, matched_by=how)
                    else:
                        row["status"] = "NO-ABSTRACT"
                        row["matched_by"] = how
                else:
                    row["status"] = "NOT-IN-STORE"
            rows.append(row)
    return rows, missing


def write_worksheet(rows, missing, args):
    n = {s: sum(1 for r in rows if r["status"] == s)
         for s in ("PAIRED", "NO-ABSTRACT", "NOT-IN-STORE", "NO-BIB-ENTRY")}
    import datetime
    L = [f"# cite-check worksheet -- `{os.path.basename(args.tex)}`", "",
         f"*Generated {datetime.date.today().isoformat()}. {len(rows)} citation "
         f"instances across {len({r['key'] for r in rows})} keys. The agent judges "
         f"each PAIRED item per the SKILL.md rules (quote the abstract verbatim; "
         f"never invent support). This file is machine output -- the judged report "
         f"is a separate file.*", "",
         f"- PAIRED (ready to judge): {n['PAIRED']}",
         f"- NO-ABSTRACT (in store, abstract withheld -- screen fulltext/DOI): {n['NO-ABSTRACT']}",
         f"- NOT-IN-STORE (run lit-review lookup/enrich to ground it): {n['NOT-IN-STORE']}",
         f"- NO-BIB-ENTRY (cited key missing from .bib -- hard error): {n['NO-BIB-ENTRY']}", ""]
    by_key = {}
    for r in rows:
        by_key.setdefault(r["key"], []).append(r)
    for key in sorted(by_key):
        rs = by_key[key]
        L.append(f"## `{key}` -- {rs[0]['title'] or '(no .bib title)'}")
        L.append(f"*status: {rs[0]['status']}"
                 + (f", matched by {rs[0]['matched_by']}" if rs[0]["matched_by"] else "")
                 + "*")
        L.append("")
        for i, r in enumerate(rs, 1):
            L.append(f"{i}. **claim:** {r['sentence']}")
        if rs[0]["abstract"]:
            L.append("")
            L.append(f"> **{rs[0]['abstract_src']}:** {rs[0]['abstract'][:1200]}")
        L.append("")
    out_md = os.path.join(args.out_dir, "cite_check_worksheet.md")
    out_js = os.path.join(args.out_dir, "cite_check_worksheet.json")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    with open(out_js, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"[cite-check] {len(rows)} citation instances: " +
          ", ".join(f"{k}={v}" for k, v in n.items()))
    print(f"[cite-check] wrote {out_md} (+ .json)")
    if missing:
        print(f"[cite-check] FAIL: cited keys missing from the .bib: "
              f"{', '.join(sorted(missing))}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tex", required=True, help="LaTeX manuscript")
    ap.add_argument("--bib", required=True, help="its bibliography")
    ap.add_argument("--papers", nargs="*", default=[],
                    help="glob(s) of lit-review papers.json stores (abstract source)")
    ap.add_argument("--out-dir", default=None, help="default: the manuscript's folder")
    args = ap.parse_args()
    args.out_dir = args.out_dir or os.path.dirname(os.path.abspath(args.tex))
    rows, missing = build(args)
    write_worksheet(rows, missing, args)
    sys.exit(1 if missing else 0)


if __name__ == "__main__":
    main()

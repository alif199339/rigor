"""
ref_audit.py -- reviewer-side reference verification for submitted PDF papers.

Extracts the reference list from a PDF (IEEE numbered style first, author-start
fallback), parses each entry, and verifies it against Semantic Scholar + Crossref
(+ Retraction Watch via OpenAlex) using bib-audit's lookup engine. The target
failure is the HALLUCINATED reference: a citation that resolves to no existing
work -- the classic fingerprint of unverified AI-generated content.

Confidentiality: only the CITED works' title/DOI/arXiv strings are sent to the
APIs -- metadata of already-published works. The submitted paper's own title,
authors, abstract, and body text never leave the machine.

The script reports; it never judges intent. NOT-FOUND is a candidate for the
human/agent adjudication pass (non-indexed venues, standards, and URLs are
legitimate misses), not proof of fabrication.

Needs pypdf + the bib-audit skill folder next to this one. S2_API_KEY respected
(1 req/s). Windows: set PYTHONUTF8=1.

Usage:
  python ref_audit.py --pdf paper.pdf [--mailto you@x.com] [--force]
  python ref_audit.py --dir papers/   [--mailto you@x.com] [--force]

Outputs (next to each PDF): <stem>_ref_audit.md + <stem>_ref_audit.json
(the JSON doubles as the batch checkpoint -- finished papers are skipped on
re-run unless --force). --dir also writes ref_audit_summary.md, ranked worst-first.
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys
import urllib.parse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bib-audit"))
import bib_audit as BA  # noqa: E402  (the shared verification engine -- never forked)

# severity order for reviewer-side triage: a reference that resolves to nothing
# outranks a field mismatch; unparseable entries still need human eyes.
ORDER = ["RETRACTED", "NOT-FOUND", "MISMATCH", "PARSE-FAILED",
         "UNVERIFIABLE", "NON-PAPER-OK", "VERIFIED"]

_UNPUB = ("submitted", "in press", "under review", "to appear")


# ---------------- PDF text ----------------

def extract_pages(pdf_path):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise SystemExit("[ref-audit] pypdf is not installed in this interpreter "
                         "(`python -m pip install pypdf`).")
    reader = PdfReader(pdf_path)
    return [(page.extract_text() or "") for page in reader.pages]


def find_references(pages):
    """Return (refs_text, page_of_offset fn) -- text from the LAST heading-like
    'References' occurrence to the end of the document, plus a char-offset ->
    page-number resolver so each entry can be located for the report."""
    bounds, full, off = [], [], 0
    for i, ptxt in enumerate(pages, 1):
        bounds.append((off, i))
        full.append(ptxt)
        off += len(ptxt) + 1
    text = "\n".join(full)

    def page_of(pos):
        pg = 1
        for start, num in bounds:
            if pos >= start:
                pg = num
        return pg

    last = None
    for m in re.finditer(r"(?:^|\n)\s*(?:[IVXLC]+\.\s*|\d+\.?\s*)?(REFERENCES|References)\s*(?:\n|$)",
                         text):
        last = m
    if last:
        return text[last.end():], last.end(), page_of
    # no heading found (mangled extraction) -- fall back to the last 30% of the doc
    cut = int(len(text) * 0.7)
    return text[cut:], cut, page_of


def split_numbered(refs_text):
    """IEEE numbered style: accept a [n] marker only when n continues the 1,2,3...
    sequence, which skips inline citation brackets inside reference strings."""
    markers = [(m.start(), int(m.group(1))) for m in re.finditer(r"\[(\d{1,3})\]", refs_text)]
    starts, expect = [], 1
    for pos, n in markers:
        if n == expect:
            starts.append((pos, n))
            expect += 1
    entries = []
    for i, (pos, n) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(refs_text)
        entries.append((n, pos, refs_text[pos:end]))
    return entries


def split_author_start(refs_text):
    """Fallback for author-year styles: split on lines that open with an author
    pattern ('Lastname, F.' / 'F. Lastname')."""
    parts = re.split(r"\n(?=(?:[A-Z][A-Za-z\-']+,\s+[A-Z]\.|[A-Z]\.\s*[A-Z]?\.?\s+[A-Z][A-Za-z\-']+,))",
                     refs_text)
    out, pos = [], 0
    for i, p in enumerate(parts, 1):
        if len(p.strip()) > 20:
            out.append((i, pos, p))
        pos += len(p) + 1
    return out


def clean_entry(raw):
    raw = re.sub(r"-\s*\n\s*", "", raw)          # undo line-break hyphenation
    return re.sub(r"\s+", " ", raw).strip()


# ---------------- per-entry parsing (IEEE grammar first) ----------------

def guess_title(body):
    """Unquoted styles ('Authors. Title. Venue, vol:pp, year.'): split into
    period-delimited segments (a period after an initial or a digit does not
    split), and take the first segment that reads like a title -- >=4 words with
    a real share of lowercase-starting words (author lists are capitalized)."""
    segs = re.split(r"(?<!\s[A-Z])(?<![0-9])\.(?=[\sA-Za-z“\"])", body)
    for seg in segs:
        s = seg.strip(" ,;:")
        words = [w for w in re.findall(r"[A-Za-z][\w\-']*", s)]
        if len(words) < 4 or len(s) < 20:
            continue
        if re.match(r"(?:in\s+)?(?:proc\b|proceedings\b)", s, re.I):
            continue
        if re.search(r"\b(?:vol|no|pp)\.\s*\d|\d+\s*[:(]\s*\d", seg):
            continue
        lower = sum(1 for w in words if w[0].islower() and w not in ("et", "al", "and"))
        if lower / len(words) >= 0.35:
            return s
    return None


def parse_ref(num, raw):
    r = {"num": num, "raw": raw, "title": None, "year": None, "doi": None,
         "arxiv": None, "url": None, "venue": None, "note": None,
         "heuristic_title": False}
    m = re.search(r"\b(10\.\d{4,9}/[^\s,;]+)", raw)
    if m:
        r["doi"] = m.group(1).rstrip(".,;)")
    m = re.search(r"arXiv[:\s]+(\d{4}\.\d{4,5})", raw, re.I)
    if m:
        r["arxiv"] = m.group(1)
    m = re.search(r"(https?://[^\s]+)", raw)
    if m:
        r["url"] = m.group(1).rstrip(".,;)")
    m = re.search(r"[“\"](.+?)[”\"]", raw)   # IEEE: title inside quotes
    if m:
        r["title"] = m.group(1).strip().rstrip(",.").strip()
        tail = raw[m.end():]
        v = re.split(r",?\s*(?:vol\.|no\.|pp\.|p\.|doi|(?:19|20)\d{2})", tail)[0]
        r["venue"] = re.sub(r"^\s*(?:in\s+)?", "", v).strip(" ,.") or None
    else:                                     # unquoted styles: heuristic segment pick
        t = guess_title(re.sub(r"^\s*\[\d+\]\s*", "", raw))
        if t:
            r["title"], r["heuristic_title"] = t, True
    years = re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", raw)
    if years:
        r["year"] = years[-1]
    if any(w in raw.lower() for w in _UNPUB):
        r["note"] = ("entry text says " +
                     "/".join(w for w in _UNPUB if w in raw.lower()) +
                     " -- a NOT-FOUND here may be benign; adjudicate before flagging.")
    return r


# ---------------- verification (bib-audit engine underneath) ----------------

def audit_ref(pr):
    """One parsed reference -> a bib-audit-style result dict (+ raw/num carried)."""
    res = {"key": f"[{pr['num']}]", "num": pr["num"], "raw": pr["raw"],
           "type": "ref", "title": pr["title"], "year": pr["year"],
           "venue": pr["venue"], "doi": pr["doi"], "url": pr["url"],
           "verdict": None, "record": None, "diffs": [], "suggest": [], "notes": []}
    if pr["note"]:
        res["notes"].append(pr["note"])

    # 1) an arXiv ID is directly checkable -- a non-resolving one is a hard signal
    if pr["arxiv"]:
        rec = BA.rec_from_s2(BA.s2_get(
            f"/paper/arXiv:{urllib.parse.quote(pr['arxiv'])}?fields={BA.S2_FIELDS}"))
        if rec:
            res["record"] = rec
            sim = BA.title_sim(pr["title"], rec.get("title") or "") if pr["title"] else None
            if sim is not None and sim < 0.80:
                res["diffs"].append(("title", pr["title"], rec.get("title"), f"similarity {sim:.2f}"))
                res["verdict"] = "MISMATCH"
                res["notes"].append("arXiv ID resolves, but to a DIFFERENT title than printed.")
            else:
                res["verdict"] = "VERIFIED"
                res["notes"].append(f"verified via arXiv:{pr['arxiv']} on Semantic Scholar.")
            _retraction(res, rec.get("doi"))
            return res
        res["notes"].append(f"arXiv:{pr['arxiv']} does NOT resolve on Semantic Scholar "
                            "-- fabricated-ID signal unless very recent.")

    # 2) DOI without a parseable title: existence check by DOI alone
    if pr["doi"] and not pr["title"]:
        rec = BA.s2_by_doi(pr["doi"]) or BA.crossref_by_doi(pr["doi"])
        if rec:
            res["record"], res["verdict"] = rec, "VERIFIED"
            res["notes"].append("verified by DOI (title not parseable from the PDF text).")
            _retraction(res, pr["doi"])
        else:
            res["verdict"] = "NOT-FOUND"
            res["notes"].append("DOI printed in the reference resolves in neither S2 nor Crossref.")
        return res

    # 3) URL-only entry (website/dataset/standard/software)
    if not pr["title"] and pr["url"]:
        entry = {"type": "misc", "key": res["key"],
                 "fields": {"howpublished": pr["url"], "year": pr["year"] or ""}}
        out = BA.audit_entry(entry)
        res.update({k: out[k] for k in ("verdict", "notes", "record", "diffs", "suggest")})
        return res

    # 4) nothing verifiable parsed
    if not pr["title"]:
        res["verdict"] = "PARSE-FAILED"
        res["notes"].append("No quoted title, DOI, arXiv ID, or URL could be parsed -- "
                            "verify this entry by hand (see the raw text).")
        return res

    # 5) the standard path: title (+year/venue/doi) through bib-audit's audit_entry.
    #    No 'note' field is passed, so 'preprint'-style wording cannot soften a
    #    NOT-FOUND into UNVERIFIABLE -- the adjudication pass owns that call.
    if pr.get("heuristic_title"):
        res["notes"].append("title parsed HEURISTICALLY (unquoted reference style) -- on a "
                            "NOT-FOUND/MISMATCH, first check the raw text for a mis-picked segment.")
    fields = {"title": pr["title"], "year": pr["year"] or "",
              "journal": pr["venue"] or ""}
    if pr["doi"]:
        fields["doi"] = pr["doi"]
    out = BA.audit_entry({"type": "article", "key": res["key"], "fields": fields})
    if (out["verdict"] == "NOT-FOUND" and pr["doi"]
            and any("DOI resolves in neither" in n for n in out["notes"])):
        # PDF line-wrap routinely damages printed DOIs -- that is an extraction
        # artifact, not evidence against the work. Retry on the title alone; the
        # non-resolving DOI stays on record for the adjudicator.
        res["notes"].append(f"printed DOI `{pr['doi']}` resolves in neither S2 nor Crossref "
                            "(PDF line-wrap often damages DOIs) -- retried by title match.")
        del fields["doi"]
        out = BA.audit_entry({"type": "article", "key": res["key"], "fields": fields})
    for k in ("verdict", "record", "diffs", "suggest"):
        res[k] = out[k]
    res["notes"].extend(out["notes"])
    return res


def _retraction(res, doi):
    if not doi:
        return
    if BA.openalex_retracted(doi):
        res["verdict"] = "RETRACTED"
        res["notes"].insert(0, "OpenAlex (Retraction Watch data) marks this work RETRACTED.")


# ---------------- per-paper run ----------------

def audit_pdf(pdf_path, force=False):
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    out_dir = os.path.dirname(os.path.abspath(pdf_path))
    jpath = os.path.join(out_dir, f"{stem}_ref_audit.json")
    mpath = os.path.join(out_dir, f"{stem}_ref_audit.md")

    if os.path.exists(jpath) and not force:
        try:
            done = json.load(open(jpath, encoding="utf-8"))
            if done.get("complete"):
                print(f"  [skip] {stem} -- already audited (--force to redo)")
                return done
        except Exception:
            pass

    print(f"[ref-audit] {stem}")
    pages = extract_pages(pdf_path)
    refs_text, base_off, page_of = find_references(pages)
    entries = split_numbered(refs_text)
    style = "ieee-numbered"
    if len(entries) < 3:
        entries = split_author_start(refs_text)
        style = "author-start-fallback"
    doc = {"pdf": os.path.basename(pdf_path), "paper_id": stem,
           "generated": datetime.date.today().isoformat(),
           "style": style, "n_entries": len(entries), "complete": False, "results": []}

    if len(entries) < 3:
        doc["results"].append({"key": "[?]", "num": 0, "raw": refs_text[:600],
                               "verdict": "PARSE-FAILED", "title": None, "year": None,
                               "venue": None, "doi": None, "url": None, "record": None,
                               "diffs": [], "suggest": [],
                               "notes": ["Could not locate a parseable reference list in the "
                                         "extracted text -- the agent must read the PDF's "
                                         "references pages directly."]})
        doc["complete"] = True
        _save(jpath, doc)
        write_report(doc, mpath)
        return doc

    for n, pos, raw in entries:
        pr = parse_ref(n, clean_entry(raw))
        print(f"  [{n}] {(pr['title'] or pr['url'] or pr['raw'])[:70]} ...", flush=True)
        r = audit_ref(pr)
        r["page"] = page_of(base_off + pos)
        doc["results"].append(r)
        _save(jpath, doc)          # checkpoint after every entry
    doc["complete"] = True
    _save(jpath, doc)
    write_report(doc, mpath)
    counts = _counts(doc)
    print("  -> " + ", ".join(f"{k}={v}" for k, v in counts.items() if v))
    return doc


def _save(path, doc):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)


def _counts(doc):
    return {v: sum(1 for r in doc["results"] if r["verdict"] == v) for v in ORDER}


# ---------------- reports ----------------

def write_report(doc, out_path):
    counts = _counts(doc)
    L = [f"# Reference audit -- `{doc['pdf']}`  (paper ID: {doc['paper_id']})", "",
         f"*Generated {doc['generated']} by `ref_audit.py` against Semantic Scholar + "
         f"Crossref (+ Retraction Watch via OpenAlex). {len(doc['results'])} reference "
         f"entries ({doc['style']}). This report flags CANDIDATES with evidence; the "
         f"adjudication pass and the technical chair own the judgment. Only cited-work "
         f"metadata was sent to the APIs -- never the paper's own text.*", "",
         "## Summary", "",
         "| Verdict | Count | Meaning |", "|---|---|---|",
         f"| RETRACTED | {counts['RETRACTED']} | cited work is retracted (Retraction Watch) |",
         f"| NOT-FOUND | {counts['NOT-FOUND']} | resolves to no record in S2 or Crossref -- hallucination candidate |",
         f"| MISMATCH | {counts['MISMATCH']} | a record exists but a field conflicts (title/year drift) |",
         f"| PARSE-FAILED | {counts['PARSE-FAILED']} | entry could not be parsed -- verify by hand |",
         f"| UNVERIFIABLE | {counts['UNVERIFIABLE']} | no verifiable handle (unpublished etc.) |",
         f"| NON-PAPER-OK | {counts['NON-PAPER-OK']} | dataset/standard/software; URL resolves |",
         f"| VERIFIED | {counts['VERIFIED']} | matches a real record |",
         "", "| Ref | Verdict | p. | Headline |", "|---|---|---|---|"]
    ranked = sorted(doc["results"], key=lambda r: (ORDER.index(r["verdict"]), r["num"]))
    for r in ranked:
        head = ""
        if r["diffs"]:
            head = "; ".join(f"{d[0]}: `{d[1]}` -> `{d[2]}`" for d in r["diffs"])[:90]
        elif r["notes"]:
            head = r["notes"][0][:90]
        L.append(f"| {r['key']} | **{r['verdict']}** | {r.get('page', '?')} | "
                 f"{head.replace(chr(124), '/')} |")

    L += ["", "## Details (worst first)", ""]
    for r in ranked:
        if r["verdict"] == "VERIFIED" and not r["diffs"]:
            continue
        L.append(f"### {r['key']} -- {r['verdict']}  (p.{r.get('page', '?')})")
        L.append("")
        L.append(f"- **as printed:** {r['raw'][:400]}")
        if r["title"]:
            L.append(f"- parsed: title=“{r['title']}” year={r['year'] or '?'} "
                     f"venue={r['venue'] or '?'}" + (f" DOI={r['doi']}" if r["doi"] else ""))
        rec = r.get("record")
        if rec:
            L.append(f"- **found ({rec['src']}):** {rec.get('title')} -- {rec.get('year')} -- "
                     f"{rec.get('venue')}" + (f" -- DOI {rec['doi']}" if rec.get("doi") else ""))
        for d in r["diffs"]:
            extra = f" ({d[3]})" if len(d) > 3 else ""
            L.append(f"- **diff [{d[0]}]:** printed = `{d[1]}`  vs  found = `{d[2]}`{extra}")
        for nt in r["notes"]:
            L.append(f"- note: {nt}")
        L.append("")
    n_verified = counts["VERIFIED"]
    L += [f"*{n_verified} VERIFIED entries without field drift are not detailed individually.*", ""]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"  [report] {out_path}")


def write_summary(dirpath):
    docs = []
    for j in sorted(glob.glob(os.path.join(dirpath, "*_ref_audit.json"))):
        try:
            docs.append(json.load(open(j, encoding="utf-8")))
        except Exception:
            continue
    if not docs:
        return
    rows = []
    for d in docs:
        c = _counts(d)
        rows.append((d["paper_id"], len(d["results"]), c))
    rows.sort(key=lambda r: (-r[2]["RETRACTED"], -r[2]["NOT-FOUND"], -r[2]["MISMATCH"],
                             -r[2]["PARSE-FAILED"], r[0]))
    out = os.path.join(dirpath, "ref_audit_summary.md")
    L = [f"# Reference audit -- batch summary ({len(docs)} papers)", "",
         f"*Generated {datetime.date.today().isoformat()}. Ranked worst-first "
         f"(retracted > not-found > mismatch). Every flag is a candidate for "
         f"adjudication, not a verdict on the authors.*", "",
         "| Paper ID | refs | RETRACTED | NOT-FOUND | MISMATCH | PARSE-FAILED | VERIFIED |",
         "|---|---|---|---|---|---|---|"]
    for pid, n, c in rows:
        mark = "**" if (c["RETRACTED"] or c["NOT-FOUND"]) else ""
        L.append(f"| {mark}{pid}{mark} | {n} | {c['RETRACTED']} | {c['NOT-FOUND']} | "
                 f"{c['MISMATCH']} | {c['PARSE-FAILED']} | {c['VERIFIED']} |")
    L.append("")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"[summary] {out}")


# ---------------- CLI ----------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pdf", help="audit a single PDF")
    g.add_argument("--dir", help="audit every *.pdf in a directory (checkpointed)")
    ap.add_argument("--mailto", default=None, help="Crossref polite-pool contact email")
    ap.add_argument("--force", action="store_true", help="re-audit even if a JSON checkpoint exists")
    args = ap.parse_args()

    if args.mailto:
        os.environ["CROSSREF_MAILTO"] = args.mailto

    if args.pdf:
        audit_pdf(args.pdf, force=args.force)
        return
    pdfs = sorted(glob.glob(os.path.join(args.dir, "*.pdf")))
    if not pdfs:
        raise SystemExit(f"[ref-audit] no PDFs in {args.dir}")
    print(f"[ref-audit] batch: {len(pdfs)} PDFs in {args.dir} (S2 is 1 req/s -- "
          f"expect roughly 1-2 min per paper)")
    for p in pdfs:
        try:
            audit_pdf(p, force=args.force)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"  [error] {os.path.basename(p)}: {e}", file=sys.stderr)
    write_summary(args.dir)


if __name__ == "__main__":
    main()

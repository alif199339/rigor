"""
result_audit.py -- internal data/result/performance-consistency screening for
submitted PDF papers (reviewer-side; the PDF is the only input).

A paper must at least agree with ITSELF. This script extracts page-tagged text
and emits inconsistency CANDIDATES, each with page number + quoted context:

  NEAR-MISS   a headline number in the abstract/conclusion that is CLOSE to a
              value in the body/tables but off beyond its printed precision
              (the classic "text says 95.2, Table III says 94.8" drift).
  ORPHAN      a results-like number in the abstract/conclusion that appears
              NOWHERE in the body -- untraceable claims are the fabrication class.
  ARITH       a same-sentence "improves/reduces by N%" that no pair of numbers
              in that sentence can reproduce (absolute or percentage-point).
  SPLIT       a train/val/test percentage split that does not sum to ~100.
  IMPOSSIBLE  out-of-range statistics: p > 1 or p = 0, accuracy/percent > 100,
              |r| > 1, negative error metrics; with scipy present, statcheck-style
              recomputation of p from t(df)/F(df1,df2)/chi2(df) statistics.
  GRIM        a reported mean inconsistent with the reported integer N at the
              printed precision.
  XSECTION    the same named metric quoted with irreconcilable values in the
              abstract vs the conclusion.

Everything offline; nothing is sent anywhere. pypdf required; scipy optional
(without it the p-recomputation is skipped and disclosed). The script trades
false positives for coverage on lossy PDF table text -- the agent adjudication
pass (CONFIRMED-INCONSISTENT / EXPLAINED / EXTRACTION-NOISE) is the filter,
and only CONFIRMED items may reach the chair.

Usage:
  python result_audit.py --pdf paper.pdf [--force]
  python result_audit.py --dir papers/   [--force]

Outputs (next to each PDF): <stem>_result_audit.md + .json;
--dir also writes result_audit_summary.md.
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    from scipy import stats as _sst
except ImportError:
    _sst = None

METRIC = (r"(accuracy|precision|recall|f1(?:[ -]?score)?|mape|smape|rmse|mae|mse|"
          r"r2|auc|m?ap|bleu|rouge|psnr|ssim|dice|iou|error(?:\s+rate)?|loss|"
          r"latency|throughput|speedup|fps|perplexity|wer|cer)")
NUM = re.compile(r"(?<![\w.\-])(\d{1,3}(?:,\d{3})+|\d+\.\d+|\d+)(\s*%)?(?![\d.])")


# ---------------- PDF -> (page, section, line) stream ----------------

def extract_pages(pdf_path):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise SystemExit("[result-audit] pypdf is not installed in this interpreter.")
    reader = PdfReader(pdf_path)
    return [(page.extract_text() or "") for page in reader.pages]


_H_ABS = re.compile(r"\babstract\b\s*[-—:.]?", re.I)
_H_BODY = re.compile(r"(index\s+terms|keywords|\bI\.\s+INTRODUCTION\b|\b1\.?\s+Introduction\b)", re.I)
# heading-anchored: 'V. CONCLUSION AND FUTURE WORK' yes; a body sentence fragment
# containing the word 'conclusion' must NOT flip the section state machine.
_H_CONCL = re.compile(r"^\s*(?:[IVXLC]+\.?\s*|\d+\.?\s*)?"
                      r"(?:CONCLUSIONS?|Conclusions?|CONCLUDING\s+REMARKS|Concluding\s+Remarks)"
                      r"\b[\sA-Za-z]{0,40}$")
_H_BACK = re.compile(r"^\s*(acknowledg|appendix)", re.I)
_H_REFS = re.compile(r"^\s*(?:[IVXLC]+\.\s*|\d+\.?\s*)?(REFERENCES|References)\s*$")


def sectioned_lines(pages):
    """Yield (page, section, line) with section in
    front/abstract/body/conclusion/back/references (references ends collection)."""
    sec = "front"
    out = []
    for pg, ptxt in enumerate(pages, 1):
        for line in ptxt.splitlines():
            s = line.strip()
            if _H_REFS.match(s):
                return out
            if sec in ("front",) and _H_ABS.search(s):
                sec = "abstract"
            elif sec in ("front", "abstract") and _H_BODY.search(s):
                sec = "body"
            elif sec == "body" and len(s) < 60 and _H_CONCL.match(s):
                sec = "conclusion"
            elif sec == "conclusion" and _H_BACK.match(s):
                sec = "back"
            out.append((pg, sec, line))
    return out


def scrub(line):
    """Remove numbers that are never result claims: citation brackets, years,
    figure/table/section/equation references."""
    line = re.sub(r"\[\d{1,3}(?:\s*[,;–\-]\s*\d{1,3})*\]", " ", line)
    line = re.sub(r"\b(?:Fig(?:ure|s?)?\.?|Table|Section|Sec\.|Eq(?:uation)?s?\.?|"
                  r"Algorithm|Chapter|Step|Layer)\s*\(?[IVXLC\d]+\)?", " ", line, flags=re.I)
    line = re.sub(r"(?<![\d.])(?:19|20)\d{2}(?![\d.%])", " ", line)
    return line


def numbers_in(text):
    for m in NUM.finditer(text):
        tok = m.group(1)
        val = float(tok.replace(",", ""))
        yield tok, val, bool(m.group(2)), m.start()


# ---------------- claims vs pool (NEAR-MISS / ORPHAN) ----------------

def decimals(tok):
    return len(tok.split(".")[1]) if "." in tok else 0


def collect(lines):
    """claims: numeric tokens in abstract/conclusion prose; pool: every numeric
    value in the body (tables included -- pypdf renders their cells as text)."""
    claims, pool = [], []
    for pg, sec, line in lines:
        s = scrub(line)
        for tok, val, pct, pos in numbers_in(s):
            ctx = re.sub(r"\s+", " ", s[max(0, pos - 60):pos + len(tok) + 60]).strip()
            near = s[max(0, pos - 45):pos + len(tok) + 45].lower()
            metricish = pct or bool(re.search(METRIC, near, re.I))
            if sec in ("abstract", "conclusion") and metricish and ("." in tok or pct):
                claims.append({"tok": tok, "val": val, "page": pg, "sec": sec, "ctx": ctx})
            if sec == "body":
                pool.append((tok, val))
    return claims, pool


def classify(claim, pool):
    if not pool:
        return "ORPHAN", None
    c = claim["val"]
    tol = 0.5 * 10 ** -decimals(claim["tok"]) + 1e-9
    best, bd = None, float("inf")
    for tok, v in pool:
        d = abs(v - c)
        if d < bd:
            best, bd = v, d
    if bd <= tol:
        return "MATCHED", best
    if bd <= max(1.0, 0.02 * abs(c)):
        return "NEAR-MISS", best
    return "ORPHAN", best


# ---------------- same-sentence arithmetic (ARITH / SPLIT) ----------------

def sentences(lines, secs=("abstract", "body", "conclusion")):
    buf, page = [], None
    for pg, sec, line in lines:
        if sec not in secs:
            continue
        if page is None:
            page = pg
        buf.append(line.strip())
        joined = " ".join(buf)
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", joined)
        if len(parts) > 1:
            for p in parts[:-1]:
                yield page, p
            buf, page = [parts[-1]], pg
    if buf:
        yield page or 1, " ".join(buf)


_CHANGE_NOUNS = r"improvement|reduction|gain|increase|decrease|drop|boost|speedup"


def change_claims(s):
    """Percent values that are explicitly claimed as a CHANGE (not absolute
    metric values like 'Baseline-X (12.4%)') -- only these must be derivable."""
    out = []
    for m in re.finditer(r"\bby\s+(\d+(?:\.\d+)?)\s*%", s, re.I):
        out.append(float(m.group(1)))
    for m in re.finditer(r"\b(\d+(?:\.\d+)?)\s*%\s+(?:" + _CHANGE_NOUNS +
                         r"|faster|slower|lower|higher)", s, re.I):
        out.append(float(m.group(1)))
    for m in re.finditer(r"(?:" + _CHANGE_NOUNS + r")\s+of\s+(\d+(?:\.\d+)?)\s*%", s, re.I):
        out.append(float(m.group(1)))
    return out


def check_arith(lines):
    out = []
    for pg, sent in sentences(lines):
        s = scrub(sent)
        claimed = change_claims(s)
        if not claimed:
            continue
        nums = [val for _, val, _, _ in numbers_in(s)]
        m = re.search(r"from\s+(\d+(?:\.\d+)?)\s*%?\s+to\s+(\d+(?:\.\d+)?)", s, re.I)
        if m:
            nums = [float(m.group(1)), float(m.group(2))] + nums
        for c in claimed:
            cands = [v for v in nums if v != c]
            if len(cands) < 2:
                continue
            ok = False
            for a in cands:
                for b in cands:
                    if a == b:
                        continue
                    if b != 0 and abs(abs(a - b) / abs(b) * 100 - c) <= max(0.6, 0.02 * c):
                        ok = True
                    if abs(abs(a - b) - c) <= 0.6:      # percentage-point reading
                        ok = True
            if not ok:
                out.append({"page": pg, "claimed_pct": c, "operands": cands[:8],
                            "ctx": re.sub(r"\s+", " ", sent)[:240]})
    return out


def check_splits(lines):
    out = []
    for pg, sent in sentences(lines):
        if not re.search(r"\b(train|test|valid|split)\w*", sent, re.I):
            continue
        m = re.search(r"(\d{1,2})\s*[/:]\s*(\d{1,2})\s*[/:]\s*(\d{1,2})\b", sent)
        vals = [float(g) for g in m.groups()] if m else None
        if not vals:
            pcts = [v for _, v, pct, _ in numbers_in(scrub(sent)) if pct]
            vals = pcts if len(pcts) == 3 else None
        if vals and not (99.0 <= sum(vals) <= 101.0):
            out.append({"page": pg, "values": vals, "sum": sum(vals),
                        "ctx": re.sub(r"\s+", " ", sent)[:240]})
    return out


# ---------------- impossible statistics ----------------

# a stated-p number, guarded against scientific notation ('p = 5.5×10−169',
# 'p < 10 −11', 'p = 3e-8' -- pypdf renders superscripts as trailing '−NN'):
# (?!\.?\d) blocks backtracking into a partial number so the guard can't be bypassed
_PNUM = r"(\d+(?:\.\d+)?)(?!\.?\d)(?!\s*[×xX*·^−]|\s*10\s*[−-]|[eE][-+−]?\d)"


def check_impossible(lines):
    out = []

    def add(pg, what, ctx):
        out.append({"page": pg, "what": what, "ctx": re.sub(r"\s+", " ", ctx)[:240]})

    # sentence iteration joins wrapped lines ('precision of\n112.5%') that a
    # per-line pass would miss
    for pg, line in sentences(lines):
        for m in re.finditer(r"\bp\s*([<=])\s*" + _PNUM, line):
            v = float(m.group(2))
            if v > 1:
                add(pg, f"p-value {m.group(1)} {v} is outside (0, 1]", line)
            if m.group(1) == "=" and v == 0:
                add(pg, "p = 0 exactly is impossible; report p < threshold instead", line)
        for m in re.finditer(METRIC + r"\D{0,12}?(\d+(?:\.\d+)?)\s*%", line, re.I):
            if float(m.group(2)) > 100:
                add(pg, f"{m.group(1)} = {m.group(2)}% exceeds 100%", line)
        for m in re.finditer(r"\br\s*=\s*(-?\d?\.\d+)", line):
            if abs(float(m.group(1))) > 1:
                add(pg, f"correlation r = {m.group(1)} outside [-1, 1]", line)
        for m in re.finditer(r"(rmse|mae|mape|mse|error\s+rate|variance|std)\D{0,10}?"
                             r"(-\d+(?:\.\d+)?)", line, re.I):
            add(pg, f"{m.group(1)} = {m.group(2)} is negative", line)
        # statcheck-style recomputation (needs scipy)
        if _sst is not None:
            for m in re.finditer(r"\bt\s*\(\s*(\d+)\s*\)\s*=\s*(-?\d+(?:\.\d+)?)\s*,?\s*"
                                 r"p\s*([<=])\s*" + _PNUM, line):
                df, t, op, p = int(m.group(1)), float(m.group(2)), m.group(3), float(m.group(4))
                pr = 2 * _sst.t.sf(abs(t), df)
                if (op == "=" and abs(pr - p) > max(0.011, 0.1 * p)) or (op == "<" and pr >= p):
                    add(pg, f"t({df}) = {t} gives two-tailed p = {pr:.4g}, "
                            f"but the paper states p {op} {p}", line)
            for m in re.finditer(r"\bF\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*=\s*(\d+(?:\.\d+)?)\s*,?\s*"
                                 r"p\s*([<=])\s*" + _PNUM, line):
                d1, d2, F, op, p = (int(m.group(1)), int(m.group(2)), float(m.group(3)),
                                    m.group(4), float(m.group(5)))
                pr = _sst.f.sf(F, d1, d2)
                if (op == "=" and abs(pr - p) > max(0.011, 0.1 * p)) or (op == "<" and pr >= p):
                    add(pg, f"F({d1},{d2}) = {F} gives p = {pr:.4g}, "
                            f"but the paper states p {op} {p}", line)
            for m in re.finditer(r"(?:χ2|χ²|chi2|chi-square[d]?)\s*\(\s*(\d+)\s*\)\s*=\s*"
                                 r"(\d+(?:\.\d+)?)\s*,?\s*p\s*([<=])\s*" + _PNUM,
                                 line, re.I):
                df, x2, op, p = int(m.group(1)), float(m.group(2)), m.group(3), float(m.group(4))
                pr = _sst.chi2.sf(x2, df)
                if (op == "=" and abs(pr - p) > max(0.011, 0.1 * p)) or (op == "<" and pr >= p):
                    add(pg, f"chi2({df}) = {x2} gives p = {pr:.4g}, "
                            f"but the paper states p {op} {p}", line)
    return out


def check_grim(lines):
    out = []
    for pg, line in sentences(lines):
        for m in re.finditer(r"[Mm]ean\s*(?:=|of|:)?\s*(\d+\.\d{1,2})\b\W{0,20}?"
                             r"\b[Nn]\s*=\s*(\d+)\b", line):
            tok, n = m.group(1), int(m.group(2))
            if n <= 1 or n > 10000:
                continue
            mean, dec = float(tok), decimals(tok)
            k = round(mean * n)
            if abs(round(k / n, dec) - mean) > 1e-9:
                out.append({"page": pg, "mean": tok, "n": n,
                            "what": f"no integer total / {n} rounds to {tok} "
                                    f"(closest: {round(k / n, dec)})",
                            "ctx": re.sub(r"\s+", " ", line)[:240]})
    return out


# ---------------- cross-section contradiction ----------------

def check_xsection(lines):
    vals = {"abstract": {}, "conclusion": {}}
    for pg, sec, line in lines:
        if sec not in vals:
            continue
        s = scrub(line)
        for m in re.finditer(METRIC + r"\s*(?:of|=|:|is|was|reach(?:es|ed)?|achiev(?:es|ed)?|at)?"
                                      r"\s*(\d+(?:\.\d+)?)\s*%?", s, re.I):
            name = re.sub(r"\s+", " ", m.group(1).lower().strip())
            vals[sec].setdefault(name, []).append(
                (m.group(2), float(m.group(2)), pg, re.sub(r"\s+", " ", line)[:200]))
    out = []
    for name in set(vals["abstract"]) & set(vals["conclusion"]):
        A, C = vals["abstract"][name], vals["conclusion"][name]
        compatible = any(abs(a[1] - c[1]) <= 0.5 * 10 ** -min(decimals(a[0]), decimals(c[0])) + 1e-9
                         for a in A for c in C)
        if not compatible:
            out.append({"metric": name,
                        "abstract": [(a[0], a[2], a[3]) for a in A],
                        "conclusion": [(c[0], c[2], c[3]) for c in C]})
    return out


# ---------------- per-paper run ----------------

def audit_pdf(pdf_path, force=False):
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    out_dir = os.path.dirname(os.path.abspath(pdf_path))
    jpath = os.path.join(out_dir, f"{stem}_result_audit.json")
    if os.path.exists(jpath) and not force:
        try:
            done = json.load(open(jpath, encoding="utf-8"))
            if done.get("complete"):
                print(f"  [skip] {stem} -- already audited (--force to redo)")
                return done
        except Exception:
            pass

    print(f"[result-audit] {stem}")
    lines = sectioned_lines(extract_pages(pdf_path))
    secs = {s for _, s, _ in lines}
    claims, pool = collect(lines)
    cands, cid = [], 0

    def add(check, sev, page, detail, ctx):
        nonlocal cid
        cid += 1
        cands.append({"id": f"C{cid}", "check": check, "severity_hint": sev,
                      "page": page, "detail": detail, "ctx": ctx})

    matched = 0
    for c in claims:
        bucket, best = classify(c, pool)
        if bucket == "MATCHED":
            matched += 1
        elif bucket == "NEAR-MISS":
            add("NEAR-MISS", "high", c["page"],
                f"{c['sec']} states `{c['tok']}` but the closest body/table value is "
                f"`{best:g}` -- beyond the printed precision", c["ctx"])
        else:
            add("ORPHAN", "medium", c["page"],
                f"{c['sec']} states `{c['tok']}` which appears nowhere in the body/tables "
                f"(closest: {best:g})" if best is not None else
                f"{c['sec']} states `{c['tok']}` and the body has no numeric pool",
                c["ctx"])
    for a in check_arith(lines):
        add("ARITH", "high", a["page"],
            f"claimed {a['claimed_pct']:g}% is reproduced by no pair of the sentence's "
            f"numbers {a['operands']} (neither relative nor percentage-point)", a["ctx"])
    for s in check_splits(lines):
        add("SPLIT", "medium", s["page"],
            f"split {'/'.join(f'{v:g}' for v in s['values'])} sums to {s['sum']:g}, not 100",
            s["ctx"])
    for i in check_impossible(lines):
        add("IMPOSSIBLE", "high", i["page"], i["what"], i["ctx"])
    for g_ in check_grim(lines):
        add("GRIM", "medium", g_["page"],
            f"mean {g_['mean']} with N={g_['n']}: {g_['what']}", g_["ctx"])
    for x in check_xsection(lines):
        pgs = sorted({p for _, p, _ in x["abstract"]} | {p for _, p, _ in x["conclusion"]})
        add("XSECTION", "high", pgs[0] if pgs else 1,
            f"metric '{x['metric']}': abstract quotes "
            f"{[v for v, _, _ in x['abstract']]} vs conclusion "
            f"{[v for v, _, _ in x['conclusion']]} -- no pair agrees at printed precision",
            " || ".join(q for _, _, q in (x["abstract"] + x["conclusion"])[:2]))

    doc = {"pdf": os.path.basename(pdf_path), "paper_id": stem,
           "generated": datetime.date.today().isoformat(),
           "sections_detected": sorted(secs), "scipy": _sst is not None,
           "n_claims": len(claims), "n_matched": matched, "n_pool": len(pool),
           "complete": True, "candidates": cands}
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    write_report(doc, os.path.join(out_dir, f"{stem}_result_audit.md"))
    print(f"  -> {len(claims)} claims ({matched} matched), {len(cands)} candidates")
    return doc


ORDER = ["IMPOSSIBLE", "ARITH", "NEAR-MISS", "XSECTION", "GRIM", "SPLIT", "ORPHAN"]


def write_report(doc, out_path):
    by = {k: [c for c in doc["candidates"] if c["check"] == k] for k in ORDER}
    L = [f"# Result-consistency audit -- `{doc['pdf']}`  (paper ID: {doc['paper_id']})", "",
         f"*Generated {doc['generated']} by `result_audit.py` (fully offline). "
         f"{doc['n_claims']} headline claims checked against {doc['n_pool']} body/table "
         f"values ({doc['n_matched']} matched); {len(doc['candidates'])} CANDIDATES below. "
         f"PDF text extraction is lossy -- every item needs the adjudication pass "
         f"(CONFIRMED-INCONSISTENT / EXPLAINED / EXTRACTION-NOISE) against the actual "
         f"PDF pages before it may reach the chair.*", ""]
    if not doc.get("scipy"):
        L += ["*scipy not present in this interpreter: statcheck-style p-value "
              "recomputation was SKIPPED (range checks still ran).*", ""]
    if "abstract" not in doc.get("sections_detected", []):
        L += ["*WARNING: no Abstract section was detected -- section mapping may be "
              "unreliable for this PDF; adjudicate with extra care.*", ""]
    L += ["## Summary", "", "| Check | Candidates |", "|---|---|"]
    for k in ORDER:
        L.append(f"| {k} | {len(by[k])} |")
    L += ["", "## Candidates (adjudicate each; cite the id in the adjudication file)", ""]
    for k in ORDER:
        if not by[k]:
            continue
        L.append(f"### {k} ({len(by[k])})")
        L.append("")
        L.append("| id | p. | finding | context |")
        L.append("|---|---|---|---|")
        for c in by[k]:
            L.append(f"| {c['id']} | {c['page']} | {c['detail'].replace(chr(124), '/')} | "
                     f"…{c['ctx'].replace(chr(124), '/')[:150]}… |")
        L.append("")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"  [report] {out_path}")


def write_summary(dirpath):
    docs = []
    for j in sorted(glob.glob(os.path.join(dirpath, "*_result_audit.json"))):
        try:
            docs.append(json.load(open(j, encoding="utf-8")))
        except Exception:
            continue
    if not docs:
        return
    rows = []
    for d in docs:
        c = {k: sum(1 for x in d["candidates"] if x["check"] == k) for k in ORDER}
        high = sum(1 for x in d["candidates"] if x["severity_hint"] == "high")
        rows.append((d["paper_id"], high, c, len(d["candidates"])))
    rows.sort(key=lambda r: (-r[1], -r[3], r[0]))
    out = os.path.join(dirpath, "result_audit_summary.md")
    L = [f"# Result-consistency audit -- batch summary ({len(docs)} papers)", "",
         f"*Generated {datetime.date.today().isoformat()}. Candidate counts BEFORE "
         f"adjudication -- these are leads, not findings.*", "",
         "| Paper ID | high-sev | " + " | ".join(ORDER) + " | total |",
         "|---|---|" + "---|" * (len(ORDER) + 1)]
    for pid, high, c, tot in rows:
        mark = "**" if high else ""
        L.append(f"| {mark}{pid}{mark} | {high} | " +
                 " | ".join(str(c[k]) for k in ORDER) + f" | {tot} |")
    L.append("")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"[summary] {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pdf", help="audit a single PDF")
    g.add_argument("--dir", help="audit every *.pdf in a directory")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.pdf:
        audit_pdf(args.pdf, force=args.force)
        return
    pdfs = sorted(glob.glob(os.path.join(args.dir, "*.pdf")))
    if not pdfs:
        raise SystemExit(f"[result-audit] no PDFs in {args.dir}")
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

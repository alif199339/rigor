"""
claims_audit.py -- reconcile numeric claims in a manuscript against ground-truth data.

The manuscript's prose/abstract/captions quote numbers (MAPEs, param counts, window
counts, gaps). The ground truth is the \\input-ed table fragments (aggregator-generated)
plus the aggregated results.json. Numbers drift: a table gets regenerated after a
re-sweep but the prose keeps the old value. This script finds every numeric claim in the
prose and classifies it against the ground-truth pool.

It extracts CANDIDATES; it never edits the manuscript. Claude does the semantic
adjudication (a "3.46" in a historical-context sentence is fine; in a present-tense
results claim it is not). Buckets:
  MATCHED    -- equals a ground-truth number within rounding.
  NEAR-MISS  -- close to a ground-truth number but off beyond rounding (likely stale drift).
  ORPHAN     -- appears NOWHERE in ground truth (derived value, structural constant, or
                fabricated -- the dangerous class; read the context).
Plus a figure-staleness pass (results-derived figure older than the newest results.json)
and a list of \\pend[...] pending placeholders still in the body.

Stdlib only. Run:  python claims_audit.py --tex path/to/main.tex --tables path/to/tables \
                        --results "<sweep_dir>/runs/*/output/results.json" [--out report.md]
"""
import argparse
import glob
import json
import os
import re
import statistics as stats
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Study naming is Layer-2 config (same mechanism as stat_check.py): an optional JSON map
# at _shared/studies.json (or via --studies) with "notebook.ipynb::span_start" keys. Map
# present -> unknown runs skipped (curated mode); no map -> auto-named (generic mode).
DEFAULT_STUDIES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "_shared", "studies.json")


def load_study_map(path):
    p = path or DEFAULT_STUDIES
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    if path:
        raise SystemExit(f"[claims-audit] studies map not found: {path}")
    return None


def study_for(notebook, span, study_map):
    if study_map is not None:
        return study_map.get(f"{notebook or ''}::{span or ''}")
    if not notebook:
        return None
    stem = os.path.splitext(notebook)[0]
    return f"{stem}@{span}" if span else stem


NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+\.\d+)(?![\d])")


def as_float(tok):
    return float(tok.replace(",", ""))


# ---------------- manuscript side ----------------

def load_body(path):
    text = open(path, encoding="utf-8").read()
    m = re.search(r"\\begin\{document\}(.*)\\end\{document\}", text, re.S)
    return m.group(1) if m else text


def extract_figures(body):
    figs = []
    for m in re.finditer(r"\\(?:incfig|includegraphics)(?:\[[^\]]*\])?\{([^}]+)\}", body):
        figs.append(m.group(1).strip())
    return figs


def extract_pending(body):
    # both \pend[value] (a placeholder carrying a tentative number) and bare \pend
    # (an unfilled "??.??" hole -- the most important to surface before submission)
    return [(m.group(1) or "??.??") for m in re.finditer(r"\\pend(?:\[([^\]]*)\])?", body)]


def scrub_for_claims(body):
    """Blank out anything whose numbers are NOT prose claims: comments, table \\inputs,
    figure includes, refs/cites/labels, and \\pend placeholders (reported separately)."""
    body = re.sub(r"(?<!\\)%.*", "", body)                       # comments
    for cmd in ("input", "includegraphics", "incfig", "cite", "ref", "eqref", "cref",
                "Cref", "autoref", "label", "url", "usepackage", "include",
                "bibliography", "resizebox", "setlength"):
        body = re.sub(r"\\" + cmd + r"(\[[^\]]*\])?\{[^}]*\}", " ", body)
    body = re.sub(r"\\pend(\[[^\]]*\])?", " ", body)             # pending placeholders
    body = re.sub(r"\\href\{[^}]*\}", " ", body)
    # layout dimensions are never result claims: 0.46\textwidth, 3pt, 1.2cm, 0.5\linewidth
    body = re.sub(r"[\d.]+\\(?:text|line|column|page)(?:width|height)", " ", body)
    body = re.sub(r"[\d.]+\s*(?:pt|cm|mm|em|ex|in|bp|sp)\b", " ", body)
    body = body.replace("{,}", ",")                             # LaTeX thousands sep
    return body


def extract_claims(body):
    out = []
    for m in NUM.finditer(body):
        tok = m.group(1)
        ctx = re.sub(r"\s+", " ", body[max(0, m.start() - 55):m.end() + 55]).strip()
        out.append({"tok": tok, "val": as_float(tok), "ctx": ctx})
    return out


# ---------------- ground-truth side ----------------

def pool_from_tables(tables_dir):
    pool = []
    for p in sorted(glob.glob(os.path.join(tables_dir, "*.tex"))):
        txt = open(p, encoding="utf-8").read().replace("{,}", ",")
        src = os.path.basename(p)
        for m in NUM.finditer(txt):
            pool.append((as_float(m.group(1)), src))
    return pool


def pool_from_results(results_glob, study_map=None):
    latest = {}
    for p in sorted(glob.glob(results_glob)):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if d.get("smoke_test"):
            continue
        study = study_for(d.get("notebook"), d.get("span_start"), study_map)
        if study is None:
            continue
        sk = (study, d.get("seed"))
        ts = d.get("timestamp_utc") or ""
        if sk not in latest or ts > latest[sk][0]:
            latest[sk] = (ts, d)
    agg = {}  # (study, cfg, metric) -> [values]
    for (study, seed), (_ts, d) in latest.items():
        for cfg, r in d.get("results", {}).items():
            for metric in ("mape", "rmse", "mae"):
                v = r.get(metric)
                if v is None or (isinstance(v, float) and v != v):
                    continue
                agg.setdefault((study, cfg, metric), []).append(float(v))
            if r.get("params") is not None:
                agg.setdefault((study, cfg, "params"), []).append(int(r["params"]))
    pool = []
    for (study, cfg, metric), vals in agg.items():
        src = f"{study}:{cfg}:{metric}"
        m = stats.mean(vals)
        pool.append((round(m, 2), src))
        pool.append((round(m, 1), src))
        if len(vals) > 1:
            pool.append((round(stats.stdev(vals), 2), src))
        if metric == "params":
            pool.append((float(vals[0]), src))
    return pool


# ---------------- classify ----------------

def classify(c, pool):
    if not pool:
        return "ORPHAN", None, None
    best, bsrc, bd = None, None, float("inf")
    for v, src in pool:
        d = abs(v - c)
        if d < bd:
            best, bsrc, bd = v, src, d
    if c < 100:  # MAPE / std / small metric -- absolute tolerance
        if bd <= 0.005:
            return "MATCHED", best, bsrc
        if bd <= 0.10:
            return "NEAR-MISS", best, bsrc
        return "ORPHAN", best, bsrc
    rel = bd / max(c, 1)                 # counts / RMSE / MAE -- relative tolerance
    if bd < 0.5 or rel < 0.003:
        return "MATCHED", best, bsrc
    if rel < 0.05:
        return "NEAR-MISS", best, bsrc
    return "ORPHAN", best, bsrc


def looks_like_mape(c):
    return 0.3 <= c <= 15.0


# ---------------- figure staleness ----------------

def figure_staleness(figs, fig_dir, results_glob):
    newest = 0.0
    for p in glob.glob(results_glob):
        try:
            newest = max(newest, os.path.getmtime(p))
        except OSError:
            pass
    rows = []
    for f in figs:
        base = f[:-4] if f.lower().endswith((".pdf", ".png")) else f
        pdf = os.path.join(fig_dir, base + ".pdf")
        tex = os.path.join(fig_dir, base + ".tex")
        if os.path.exists(tex):
            rows.append((f, "hand-drawn (TikZ) -- results-staleness N/A", None))
            continue
        if not os.path.exists(pdf):
            rows.append((f, "MISSING from figures/", None))
            continue
        mt = os.path.getmtime(pdf)
        stale = newest and mt < newest
        rows.append((f, "STALE (older than newest results.json)" if stale else "fresh", mt))
    return rows, newest


# ---------------- report ----------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tex", required=True)
    ap.add_argument("--tables", required=True)
    ap.add_argument("--results", required=True, help="glob for results.json")
    ap.add_argument("--figures", default=None, help="default: <tex-dir>/figures")
    ap.add_argument("--studies", default=None,
                    help="JSON map {'notebook.ipynb::span': 'study_name'} "
                         "(default: _shared/studies.json if present, else auto-name)")
    ap.add_argument("--out", default=None, help="default: <tex-dir>/claims_audit_report.md")
    args = ap.parse_args()

    tex_dir = os.path.dirname(os.path.abspath(args.tex))
    fig_dir = args.figures or os.path.join(tex_dir, "figures")

    body = load_body(args.tex)
    figs = extract_figures(body)
    pending = extract_pending(body)
    claims = extract_claims(scrub_for_claims(body))

    pool = pool_from_tables(args.tables) + pool_from_results(args.results,
                                                             load_study_map(args.studies))

    for c in claims:
        c["bucket"], c["best"], c["src"] = classify(c["val"], pool)

    fig_rows, newest = figure_staleness(figs, fig_dir, args.results)

    buckets = {"MATCHED": [], "NEAR-MISS": [], "ORPHAN": []}
    for c in claims:
        buckets[c["bucket"]].append(c)

    import datetime
    L = [f"# Claims audit -- `{os.path.basename(args.tex)}`", "",
         f"*Generated {datetime.date.today().isoformat()} by `claims_audit.py`. "
         f"{len(claims)} numeric claims in prose/captions checked against "
         f"{len(pool)} ground-truth values (tables + results.json). This report flags "
         f"CANDIDATES for a human to adjudicate; it never edits the manuscript.*", "",
         "## Summary", "",
         f"- MATCHED: {len(buckets['MATCHED'])}  (equal within rounding -- fine)",
         f"- **NEAR-MISS: {len(buckets['NEAR-MISS'])}**  (close but drifted -- likely stale, check)",
         f"- **ORPHAN: {len(buckets['ORPHAN'])}**  (in no table/result -- derived, structural, or wrong)",
         f"- `\\pend` placeholders still in body: {len(pending)}  ({', '.join(pending) if pending else '-'})",
         ""]

    def dump(name, rows, note):
        L.append(f"## {name} ({len(rows)})")
        L.append(f"*{note}*")
        L.append("")
        if not rows:
            L.append("_none_\n")
            return
        L.append("| value | closest truth | Δ | likely MAPE? | context |")
        L.append("|---|---|---|---|---|")
        for c in sorted(rows, key=lambda x: (not looks_like_mape(x["val"]), x["val"])):
            best = f"{c['best']:g} ({c['src']})" if c["best"] is not None else "--"
            delta = f"{abs(c['val'] - c['best']):.3g}" if c["best"] is not None else "--"
            ctx = c["ctx"].replace("|", "/")[:120]
            L.append(f"| `{c['tok']}` | {best} | {delta} | {'yes' if looks_like_mape(c['val']) else ''} | …{ctx}… |")
        L.append("")

    dump("NEAR-MISS -- stale-drift candidates", buckets["NEAR-MISS"],
         "A number close to a real value but off beyond rounding. Prime suspects for "
         "'table re-swept, prose not updated'. Read each context.")
    dump("ORPHAN -- not found in any ground-truth source", buckets["ORPHAN"],
         "Appears in no table/result. Many are legitimate DERIVED values (a computed gap, "
         "a sum) or STRUCTURAL constants (N=9, 70/15/15). The dangerous ones are "
         "results-like numbers (see 'likely MAPE?') that should trace to a table but don't.")

    L += ["## Figure staleness", "",
          f"*Newest results.json mtime: "
          f"{datetime.datetime.fromtimestamp(newest).isoformat() if newest else 'n/a'}. "
          f"A results-derived figure older than that may need `_aggregate_paper_results.py` re-run.*",
          "", "| figure | status |", "|---|---|"]
    for f, status, _mt in fig_rows:
        flag = "**" if ("STALE" in status or "MISSING" in status) else ""
        L.append(f"| {f} | {flag}{status}{flag} |")
    L.append("")

    L += ["## MATCHED (for completeness)", "",
          f"{len(buckets['MATCHED'])} claims matched a ground-truth value within rounding "
          f"-- not listed individually.", ""]

    out = args.out or os.path.join(tex_dir, "claims_audit_report.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"[claims-audit] {len(claims)} claims: "
          f"{len(buckets['MATCHED'])} matched, {len(buckets['NEAR-MISS'])} near-miss, "
          f"{len(buckets['ORPHAN'])} orphan; {len(pending)} \\pend; "
          f"{sum('STALE' in s or 'MISSING' in s for _, s, _ in fig_rows)} stale/missing figs")
    print(f"[claims-audit] wrote {out}")


if __name__ == "__main__":
    main()

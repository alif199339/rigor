"""
stat_check.py -- paired-by-seed significance testing for "model X beats model Y" claims.

Reads every results.json under --runs-glob and groups them into studies (group by
notebook+span_start, named via an optional _shared/studies.json map; newest run per
(study, seed) supersedes; skip smoke_test; drop NaN/None mape), keeping the per-seed
values ALIGNED by seed so tests are properly paired.

For a requested model pair it computes, on the seeds present in BOTH configs:
  - Wilcoxon signed-rank (paired, non-parametric -- the primary test at n~10)
  - paired t-test (ttest_rel, for reference)
  - n, median + mean paired difference with a 95% CI, and Cohen's dz effect size
  - with --tost <margin>: a TOST equivalence test (two one-sided paired t against
    +/-margin) -- the only way to CLAIM two configs are equivalent; a failed
    difference test is not evidence of equivalence.
Optional Holm-Bonferroni correction across all tested pairs (disclosed either way).

Integrity: n=10 seeds is LOW power -- exact p-values and n are always printed, never
asterisks alone; a non-significant result is reported as such, never dropped; and
"no significant difference" is never upgraded to "equivalent/tie" without a TOST
at a pre-declared margin.

Needs scipy. Run with an interpreter that has it (the profile's python_scipy):
  python stat_check.py --runs-glob "<runs>/*/output/results.json" --list
  python stat_check.py --runs-glob "..." --study <study>                 # default battery
  python stat_check.py --runs-glob "..." --study <study> --pairs A:B --holm
"""
import argparse
import glob
import json
import os
import statistics as stats
import sys

from scipy import stats as sps

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Study naming is Layer-2 config: an optional JSON map at _shared/studies.json (or via
# --studies) with keys "notebook.ipynb::span_start" -> pretty study name. When a map is
# present, runs whose key is absent are SKIPPED (curated mode -- e.g. gpu probes). With
# no map, every run is auto-named "<notebook-stem>@<span>" (generic mode).
DEFAULT_STUDIES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "_shared", "studies.json")
# Models treated as "baselines" by the default battery (best other model vs each of
# these). Override per project with --heavy.
HEAVY_NAMES = ["MTGNN", "GTCN", "STACN", "GWN"]
DEFAULT_GLOB = "runs/*/output/results.json"


def load_study_map(path):
    p = path or DEFAULT_STUDIES
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    if path:  # explicitly requested but missing -> that's an error, not a silent fallback
        raise SystemExit(f"[stat-check] studies map not found: {path}")
    return None


def study_for(notebook, span, study_map):
    if study_map is not None:
        return study_map.get(f"{notebook or ''}::{span or ''}")
    if not notebook:
        return None
    stem = os.path.splitext(notebook)[0]
    return f"{stem}@{span}" if span else stem


def load_studies(runs_glob, study_map=None, metric_keys=("mape", "rmse", "mae")):
    """study -> config -> {seed: {mape, rmse, mae, params}}, seed-aligned."""
    latest = {}  # (study, seed) -> (timestamp, results_dict)
    for p in sorted(glob.glob(runs_glob)):
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
    studies = {}
    for (study, seed), (_ts, d) in latest.items():
        bucket = studies.setdefault(study, {})
        for cfg, r in d.get("results", {}).items():
            m = r.get("mape")
            if m is None or (isinstance(m, float) and m != m):  # None or NaN
                continue
            rec = {k: float(r[k]) for k in metric_keys if r.get(k) is not None}
            rec["params"] = r.get("params")
            bucket.setdefault(cfg, {})[seed] = rec
    return studies


def mean_of(cfg_dict, metric):
    vals = [v[metric] for v in cfg_dict.values() if metric in v]
    return stats.mean(vals) if vals else float("inf")


def test_pair(study_data, a, b, metric, tost_margin=None):
    ca, cb = study_data.get(a, {}), study_data.get(b, {})
    seeds = sorted(set(ca) & set(cb), key=lambda s: (s is None, s))
    xa = [ca[s][metric] for s in seeds if metric in ca[s] and metric in cb[s]]
    xb = [cb[s][metric] for s in seeds if metric in ca[s] and metric in cb[s]]
    n = len(xa)
    out = {"a": a, "b": b, "n": n, "mean_a": stats.mean(xa) if xa else None,
           "mean_b": stats.mean(xb) if xb else None, "w_p": None, "t_p": None,
           "median_diff": None, "mean_diff": None, "ci95": None, "dz": None,
           "tost_p": None, "tost_margin": tost_margin, "note": None}
    if n < 3:
        out["note"] = f"only {n} shared seed(s) -- too few to test"
        return out
    diffs = [x - y for x, y in zip(xa, xb)]          # a - b (lower metric = better)
    out["median_diff"] = stats.median(diffs)
    md = out["mean_diff"] = stats.mean(diffs)
    sd = stats.stdev(diffs)
    if sd > 0:
        se = sd / n ** 0.5
        h = float(sps.t.ppf(0.975, n - 1)) * se
        out["ci95"] = (md - h, md + h)
        out["dz"] = md / sd
        if tost_margin:
            # TOST: two one-sided paired t against the equivalence bounds +/-margin;
            # the reported p is the larger of the two (both must reject)
            p_lo = float(sps.t.sf((md + tost_margin) / se, n - 1))   # H0: diff <= -m
            p_hi = float(sps.t.cdf((md - tost_margin) / se, n - 1))  # H0: diff >= +m
            out["tost_p"] = max(p_lo, p_hi)
    else:
        out["ci95"] = (md, md)                        # zero variance: CI collapses
        if tost_margin:
            out["tost_p"] = 0.0 if abs(md) < tost_margin else 1.0
    if all(d == 0 for d in diffs):
        out["w_p"] = out["t_p"] = 1.0
        out["note"] = "identical on every seed"
        return out
    try:
        out["w_p"] = float(sps.wilcoxon(xa, xb).pvalue)
    except ValueError as e:
        out["note"] = f"wilcoxon: {e}"
    try:
        out["t_p"] = float(sps.ttest_rel(xa, xb).pvalue)
    except Exception:
        pass
    return out


def holm(results, alpha=0.05):
    """Holm-Bonferroni over the Wilcoxon p-values; returns key -> (adj_threshold, reject)."""
    ps = [(i, r["w_p"]) for i, r in enumerate(results) if r["w_p"] is not None]
    ps.sort(key=lambda t: t[1])
    m = len(ps)
    decisions = {}
    still_reject = True
    for rank, (i, p) in enumerate(ps):
        thr = alpha / (m - rank)
        if not (p <= thr and still_reject):
            still_reject = False
        decisions[i] = (thr, p <= thr and still_reject)
    return decisions


def fmt_p(p):
    if p is None:
        return "  n/a "
    return f"{p:.4f}" if p >= 1e-4 else f"{p:.1e}"


def default_battery(study_data, metric, heavy_names=HEAVY_NAMES):
    """Sensible pairs when the user names none: is there a distinguishable best config,
    and does the best compact config clear each heavy baseline?"""
    order = sorted(study_data.keys(), key=lambda k: mean_of(study_data[k], metric))
    pairs = []
    if len(order) >= 2:
        pairs.append((order[0], order[1]))          # best vs runner-up
    compact = [k for k in order if k not in heavy_names]
    heavy = [k for k in order if k in heavy_names]
    if compact and heavy:
        bc = compact[0]
        for h in heavy:
            pairs.append((bc, h))                    # best compact vs each heavy
    # de-dup preserving order
    seen, uniq = set(), []
    for pr in pairs:
        if pr not in seen:
            seen.add(pr)
            uniq.append(pr)
    return uniq


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--study", help="study name (see --list)")
    ap.add_argument("--metric", default="mape", choices=["mape", "rmse", "mae"])
    ap.add_argument("--pairs", help="comma-separated A:B pairs, e.g. 1A:GTCN,3A:2A")
    ap.add_argument("--holm", action="store_true", help="apply Holm-Bonferroni across tested pairs")
    ap.add_argument("--tost", type=float, metavar="MARGIN",
                    help="equivalence margin in metric units (e.g. 0.10 = 0.10 pp MAPE): "
                         "runs a TOST equivalence test per pair; required before "
                         "claiming two configs are equivalent/tied")
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--runs-glob", default=DEFAULT_GLOB)
    ap.add_argument("--studies", default=None,
                    help="JSON map {'notebook.ipynb::span': 'study_name'} "
                         "(default: _shared/studies.json if present, else auto-name)")
    ap.add_argument("--heavy", default=",".join(HEAVY_NAMES),
                    help="comma-separated model names treated as baselines by the "
                         "default battery")
    ap.add_argument("--list", action="store_true", help="list studies + configs + seeds and exit")
    ap.add_argument("--out", help="write a markdown report instead of stdout only")
    args = ap.parse_args()

    studies = load_studies(args.runs_glob, load_study_map(args.studies))
    if not studies:
        raise SystemExit(f"[stat-check] no studies found under {args.runs_glob}")

    if args.list or not args.study:
        print("Studies available:")
        for s, cfgs in sorted(studies.items()):
            seeds = sorted({sd for c in cfgs.values() for sd in c}, key=lambda x: (x is None, x))
            print(f"  {s:24s} n={len(seeds)} seeds={seeds}")
            print(f"      configs: {', '.join(sorted(cfgs.keys()))}")
        if not args.study:
            return

    if args.study not in studies:
        raise SystemExit(f"[stat-check] unknown study '{args.study}'. Use --list.")
    data = studies[args.study]

    if args.pairs:
        pairs = []
        for tok in args.pairs.split(","):
            a, _, b = tok.partition(":")
            pairs.append((a.strip(), b.strip()))
    else:
        heavy = [h.strip() for h in args.heavy.split(",") if h.strip()]
        pairs = default_battery(data, args.metric, heavy)

    results = [test_pair(data, a, b, args.metric, tost_margin=args.tost)
               for a, b in pairs]
    decisions = holm(results, args.alpha) if args.holm else {}

    tost_col = " TOST p |" if args.tost else ""
    L = [f"# stat-check -- study `{args.study}`, metric `{args.metric.upper()}`",
         "",
         f"*Paired-by-seed tests. n is the number of seeds present in BOTH configs. "
         f"n~10 is low power: read exact p-values, not just the {args.alpha} threshold. "
         f"Lower {args.metric.upper()} is better; a negative median diff means the FIRST "
         f"model wins.*",
         "",
         f"| Pair (A vs B) | n | mean A | mean B | median Δ(A−B) | mean Δ [95% CI] | dz "
         f"| Wilcoxon p | paired-t p |{tost_col} verdict |",
         "|---|---|---|---|---|---|---|---|---|" + ("---|" if args.tost else "")]
    for i, r in enumerate(results):
        if r["n"] < 3:
            skip = "-- | " * (7 + (1 if args.tost else 0))
            L.append(f"| {r['a']} vs {r['b']} | {r['n']} | {skip}{r['note']} |")
            continue
        better = r["a"] if r["mean_diff"] < 0 else r["b"]
        sig = (r["w_p"] is not None and r["w_p"] <= args.alpha)
        equiv = (args.tost and r["tost_p"] is not None and r["tost_p"] <= args.alpha)
        if args.holm and i in decisions:
            sig = decisions[i][1]
            verdict = (f"**{better} wins** (Holm-adj)" if sig
                       else f"no sig. diff (Holm thr {decisions[i][0]:.4f})")
        else:
            verdict = f"**{better} wins**" if sig else "no significant difference"
        if not sig and args.tost:
            verdict = (f"**equivalent within ±{args.tost:g}**" if equiv else
                       f"inconclusive: neither different nor equivalent at ±{args.tost:g}")
        if r["note"]:
            verdict += f" ({r['note']})"
        lo, hi = r["ci95"]
        ci = f"{r['mean_diff']:+.3f} [{lo:+.3f}, {hi:+.3f}]"
        dz = f"{r['dz']:+.2f}" if r["dz"] is not None else "n/a"
        tp = f" {fmt_p(r['tost_p'])} |" if args.tost else ""
        L.append(f"| {r['a']} vs {r['b']} | {r['n']} | {r['mean_a']:.3f} | {r['mean_b']:.3f} | "
                 f"{r['median_diff']:+.3f} | {ci} | {dz} | {fmt_p(r['w_p'])} | "
                 f"{fmt_p(r['t_p'])} |{tp} {verdict} |")

    if args.holm:
        L += ["", f"*Holm-Bonferroni correction applied across {len([r for r in results if r['n']>=3])} "
              f"testable pairs at family-wise α={args.alpha}.*"]
    else:
        L += ["", f"*No multiple-comparison correction (single/independent pairs). Add `--holm` "
              f"when scanning many pairs at once.*"]
    if args.tost:
        L += ["", f"*TOST equivalence at margin ±{args.tost:g} {args.metric.upper()} "
              f"(two one-sided paired t; the reported p is the larger of the two sides).*"]
    else:
        L += ["", "*Note: \"no significant difference\" is NOT evidence of equivalence — "
              "to claim two configs are equivalent/tied, rerun with `--tost <margin>` "
              "at a pre-declared margin.*"]

    text = "\n".join(L)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"\n[stat-check] wrote {args.out}")


if __name__ == "__main__":
    main()

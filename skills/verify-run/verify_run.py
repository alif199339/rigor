"""
verify_run.py -- the scientific-integrity checklist for a completed run, mechanized.
The last gate before any number reaches a human.

Reads one or more results.json (the schema stat-check and claims-audit share) and
reports, per run and across seeds:

  - config completeness   -- every expected config present (a missing key is a silent
                             mid-run failure, not a partial success)
  - smoke_test flag       -- pipeline-check numbers must never be read as real results
  - NaN / None metrics    -- a diverged or aborted config produced no usable number
  - futility stops        -- disclosed with reason, never hidden
  - parameter anchors      -- exact-match fingerprint that the *intended* pipeline ran
  - seed-count disclosure -- how many seeds; a single-seed number is never "robust"

Report-only and standard-library-only: it verifies and discloses; it never edits
results or reference numbers. Pair with stat-check to test whether a multi-seed
"X beats Y" gap is actually significant.

  python verify_run.py --runs-glob "runs/*/output/results.json"
  python verify_run.py --runs-glob "..." --expect 1A,1B,2A,MTGNN,GTCN
  python verify_run.py --runs-glob "..." --anchors 1A=45393,MTGNN=76705 --out verify_report.md

Exit status: 0 when nothing fails (smoke / futility / single-seed are disclosed but not
failures); 1 when any run is missing an expected config, carries a NaN/None metric, or
breaks a parameter anchor -- so it is usable as a CI / pre-report gate.
"""
import argparse
import glob
import json
import math
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

METRIC_KEYS = ("mape", "rmse", "mae")


def _disp(path):
    """Display path relative to cwd when possible, else absolute. os.path.relpath raises
    on Windows when path and cwd are on different drives (runs often live on another
    drive), so never let a cosmetic path choice crash the gate."""
    try:
        return os.path.relpath(path)
    except ValueError:
        return path


def is_bad_number(v):
    """True if a metric value is missing or non-finite (None / NaN / inf)."""
    if v is None:
        return True
    if isinstance(v, float) and not math.isfinite(v):
        return True
    return False


def load_runs(runs_glob):
    """Parse every results.json under the glob. Returns [(path, dict)], sorted by path.
    Unparseable files are surfaced as (path, None) rather than silently skipped."""
    out = []
    for p in sorted(glob.glob(runs_glob)):
        try:
            out.append((p, json.load(open(p, encoding="utf-8"))))
        except Exception as e:  # noqa: BLE001 -- report, don't crash the whole gate
            out.append((p, {"__parse_error__": str(e)}))
    return out


def parse_anchors(spec):
    """'1A=45393,MTGNN=76705' -> {'1A': 45393, 'MTGNN': 76705}."""
    anchors = {}
    if not spec:
        return anchors
    for tok in spec.split(","):
        tok = tok.strip()
        if not tok:
            continue
        name, _, val = tok.partition("=")
        try:
            anchors[name.strip()] = int(val)
        except ValueError:
            raise SystemExit(f"[verify-run] bad --anchors token '{tok}' (want NAME=INT)")
    return anchors


def check_run(path, run, expect=None, anchors=None):
    """Mechanical checks for one run dict. Returns a findings record; `fail` is True when
    a hard integrity failure is present (missing config / NaN metric / anchor mismatch)."""
    f = {
        "path": path, "seed": None, "notebook": None, "span": None,
        "smoke": False, "configs": [], "missing": [], "nan": [], "futility": [],
        "anchor_mismatch": [], "parse_error": None, "fail": False, "warn": False,
    }
    if run is None or "__parse_error__" in (run or {}):
        f["parse_error"] = (run or {}).get("__parse_error__", "empty/None")
        f["fail"] = True
        return f

    f["seed"] = run.get("seed")
    f["notebook"] = run.get("notebook")
    f["span"] = run.get("span_start")
    f["smoke"] = bool(run.get("smoke_test"))
    results = run.get("results") or {}
    f["configs"] = sorted(results.keys())

    if expect:
        f["missing"] = [c for c in expect if c not in results]

    for cfg, r in results.items():
        r = r or {}
        bad = [k for k in METRIC_KEYS if is_bad_number(r.get(k))]
        if bad:
            f["nan"].append((cfg, bad))
        fs = r.get("futility_stop")
        if fs:
            f["futility"].append((cfg, fs))
        if anchors and cfg in anchors:
            got = r.get("params")
            if got != anchors[cfg]:
                f["anchor_mismatch"].append((cfg, anchors[cfg], got))

    f["fail"] = bool(f["missing"] or f["nan"] or f["anchor_mismatch"])
    f["warn"] = bool(f["smoke"] or f["futility"])
    return f


def seed_summary(findings):
    """Group runs into studies (notebook::span) and disclose seed coverage per study
    and per config -- the check against presenting a single-seed number as robust."""
    studies = {}
    for f in findings:
        if f["parse_error"]:
            continue
        key = f"{f['notebook'] or '?'}::{f['span'] or ''}"
        st = studies.setdefault(key, {"seeds": set(), "cfg_seeds": {}})
        # smoke runs are excluded from the "real" seed census (they are not results)
        if f["smoke"]:
            continue
        st["seeds"].add(f["seed"])
        for c in f["configs"]:
            st["cfg_seeds"].setdefault(c, set()).add(f["seed"])
    return studies


def build_report(findings, studies, expect):
    L = ["# verify-run -- integrity checklist", ""]
    n_fail = sum(1 for f in findings if f["fail"])
    n_warn = sum(1 for f in findings if f["warn"] and not f["fail"])
    L.append(f"*{len(findings)} run file(s): {n_fail} with hard findings, "
             f"{n_warn} with disclosures only.*")
    L.append("")

    for f in findings:
        head = f"## `{_disp(f['path'])}`"
        if f["parse_error"]:
            L += [head, f"- **UNREADABLE**: {f['parse_error']}", ""]
            continue
        tag = "FAIL" if f["fail"] else ("DISCLOSE" if f["warn"] else "OK")
        L.append(f"{head}  — **{tag}**")
        L.append(f"- seed `{f['seed']}`, notebook `{f['notebook']}`, "
                 f"{len(f['configs'])} config(s)")
        if f["smoke"]:
            L.append("- **SMOKE_TEST run**: metrics are a pipeline check by the "
                     "notebook's own convention -- do not report them as results.")
        if f["missing"]:
            L.append(f"- **MISSING configs** (silent mid-run failure): "
                     f"{', '.join(f['missing'])}")
        for cfg, bad in f["nan"]:
            L.append(f"- **NaN/None metric** in `{cfg}`: {', '.join(bad)} "
                     f"(no usable number for this config)")
        for cfg, reason in f["futility"]:
            L.append(f"- futility stop in `{cfg}`: `{reason}` "
                     f"(reduced confidence -- disclosed, not dropped)")
        for cfg, want, got in f["anchor_mismatch"]:
            L.append(f"- **PARAM ANCHOR mismatch** in `{cfg}`: expected {want:,}, "
                     f"got {got if got is None else format(got, ',')} "
                     f"(wrong pipeline may have run)")
        L.append("")

    L.append("## Seed-count disclosure")
    if not studies:
        L.append("- (no non-smoke runs to summarize)")
    for key, st in sorted(studies.items()):
        seeds = sorted(s for s in st["seeds"] if s is not None)
        note = "  **single-seed -- not a robust average**" if len(seeds) <= 1 else ""
        L.append(f"- `{key}`: n={len(seeds)} seeds {seeds}{note}")
        if expect:
            short = [f"{c} (n={len(st['cfg_seeds'].get(c, []))})"
                     for c in expect if len(st["cfg_seeds"].get(c, [])) < len(seeds)]
            if short:
                L.append(f"    - under-covered configs: {', '.join(short)}")
    L.append("")
    L.append("*Report-only: verify-run discloses; it never edits results or reference "
             "numbers. Use stat-check to test whether a multi-seed gap is significant.*")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs-glob", default="runs/*/output/results.json",
                    help="glob for results.json files")
    ap.add_argument("--expect", default=None,
                    help="comma-separated config names every run must contain "
                         "(e.g. 1A,1B,2A,MTGNN,GTCN)")
    ap.add_argument("--anchors", default=None,
                    help="comma-separated NAME=PARAMCOUNT exact-match fingerprints "
                         "(e.g. 1A=45393,MTGNN=76705)")
    ap.add_argument("--out", default=None, help="also write the report to this markdown file")
    args = ap.parse_args()

    expect = [c.strip() for c in args.expect.split(",") if c.strip()] if args.expect else None
    anchors = parse_anchors(args.anchors)

    runs = load_runs(args.runs_glob)
    if not runs:
        raise SystemExit(f"[verify-run] no results.json found under {args.runs_glob}")

    findings = [check_run(p, r, expect, anchors) for p, r in runs]
    studies = seed_summary(findings)
    report = build_report(findings, studies, expect)
    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report + "\n")
        print(f"\n[verify-run] wrote {args.out}")

    sys.exit(1 if any(f["fail"] for f in findings) else 0)


if __name__ == "__main__":
    main()

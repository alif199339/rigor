"""submit_gate.py -- one command before submission: run the audit battery + freeze
what was submitted.

RIGOR skill: /submit-gate. Stdlib + pyyaml (config only), Python 3.10+.

  check  --config gate.yaml [--out SUBMISSION_READINESS.md]
         run every configured audit step, collect exit codes + tails into one
         readiness report; exit 1 if any required step failed.
  freeze --files "glob1" "glob2" ... [--out freeze_<date>.json]
         snapshot (sha256, bytes, mtime) of everything the submission's numbers
         rest on -- tables, results.json, the manuscript PDF -- so that months
         later, a reviewer question is answered against WHAT WAS SUBMITTED,
         not against whatever the files have since become.
  verify-freeze --against freeze_<date>.json
         recompute and report changed/missing files; exit 1 on any change.

gate.yaml (composed once per project by the agent, from project_profile.yaml):

  steps:
    - name: bib-audit
      cmd: [python, skills/bib-audit/bib_audit.py, --bib, refs.bib]
      required: true
    - name: claims-audit
      cmd: [python, skills/claims-audit/claims_audit.py, --tex, main.tex,
            --tables, tables, --results, "runs/*/output/results.json"]
      required: true

The gate runs tools; it never edits anything.
"""
import argparse
import datetime
import glob
import hashlib
import json
import os
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ---------------- check ----------------

def run_step(step):
    cmd = step["cmd"]
    if isinstance(cmd, str):
        cmd = cmd.split()
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                           encoding="utf-8", errors="replace")
        code, out = r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError as e:
        code, out = 127, f"launch failed: {e}"
    except subprocess.TimeoutExpired:
        code, out = 124, "step timed out (1800s)"
    tail = "\n".join(out.strip().splitlines()[-8:])
    return code, tail, time.time() - t0


def cmd_check(args):
    import yaml
    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    steps = cfg.get("steps") or []
    if not steps:
        raise SystemExit(f"[submit-gate] no steps in {args.config}")
    rows, hard_fail = [], False
    for s in steps:
        name, req = s.get("name", "?"), s.get("required", True)
        print(f"[gate] {name} ...", flush=True)
        code, tail, dt = run_step(s)
        ok = code == 0
        if not ok and req:
            hard_fail = True
        rows.append((name, ok, req, code, tail, dt))
        print(f"       {'PASS' if ok else 'FAIL'} (exit {code}, {dt:.0f}s)")
    L = [f"# Submission readiness -- {datetime.date.today().isoformat()}", "",
         f"*{len(rows)} gate step(s). A FAIL on a required step means NOT ready. "
         f"This report aggregates; each tool's own report has the detail.*", "",
         "| step | result | required | exit | time |", "|---|---|---|---|---|"]
    for name, ok, req, code, tail, dt in rows:
        L.append(f"| {name} | {'PASS' if ok else '**FAIL**'} | "
                 f"{'yes' if req else 'no'} | {code} | {dt:.0f}s |")
    L.append("")
    for name, ok, req, code, tail, dt in rows:
        L += [f"## {name} — {'PASS' if ok else 'FAIL'}", "", "```", tail, "```", ""]
    verdict = "NOT READY -- required step(s) failed" if hard_fail else \
        "READY (all required steps passed)"
    L += [f"**Verdict: {verdict}**", ""]
    out = args.out or "SUBMISSION_READINESS.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"[submit-gate] {verdict}")
    print(f"[submit-gate] wrote {out}")
    sys.exit(1 if hard_fail else 0)


# ---------------- freeze ----------------

def cmd_freeze(args):
    files = sorted({p for g in args.files for p in glob.glob(g, recursive=True)
                    if os.path.isfile(p)})
    if not files:
        raise SystemExit("[submit-gate] freeze: no files matched")
    snap = {"frozen": datetime.datetime.now().isoformat(timespec="seconds"),
            "label": args.label, "files": {}}
    for p in files:
        snap["files"][p.replace(os.sep, "/")] = {
            "sha256": sha256(p), "bytes": os.path.getsize(p),
            "mtime": datetime.datetime.fromtimestamp(
                os.path.getmtime(p)).isoformat(timespec="seconds")}
    out = args.out or f"freeze_{datetime.date.today().isoformat()}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=1)
    print(f"[submit-gate] froze {len(files)} file(s) -> {out}"
          + (f"  (label: {args.label})" if args.label else ""))


def cmd_verify_freeze(args):
    with open(args.against, encoding="utf-8") as f:
        snap = json.load(f)
    changed = 0
    for rel, rec in sorted(snap["files"].items()):
        p = rel.replace("/", os.sep)
        if not os.path.exists(p):
            print(f"  [FAIL] {rel}: MISSING (was frozen {snap['frozen']})")
            changed += 1
        elif sha256(p) != rec["sha256"]:
            print(f"  [FAIL] {rel}: CHANGED since freeze "
                  f"(frozen {rec['mtime']}, now "
                  f"{datetime.datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec='seconds')})")
            changed += 1
        else:
            print(f"  [ok]   {rel}")
    print(f"[submit-gate] verify-freeze: {changed} changed/missing of "
          f"{len(snap['files'])} frozen file(s)")
    sys.exit(1 if changed else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check")
    p.add_argument("--config", required=True, help="gate.yaml with the steps to run")
    p.add_argument("--out", help="default: SUBMISSION_READINESS.md (cwd)")
    p = sub.add_parser("freeze")
    p.add_argument("--files", nargs="+", required=True, help="glob(s) to snapshot")
    p.add_argument("--label", help="e.g. 'JOSS-v1' or 'IEEE-Access-round1'")
    p.add_argument("--out", help="default: freeze_<date>.json")
    p = sub.add_parser("verify-freeze")
    p.add_argument("--against", required=True)
    args = ap.parse_args()
    {"check": cmd_check, "freeze": cmd_freeze,
     "verify-freeze": cmd_verify_freeze}[args.cmd](args)


if __name__ == "__main__":
    main()

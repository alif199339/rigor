"""
colab_run.py -- semi-attended notebook execution on Google Colab via a Drive-synced folder.

Free Colab (unlike Kaggle) exposes NO official headless-execution API, and this tool
deliberately does not automate around that: execution is one human tap (Runtime -> Run
all), which doubles as the explicit GPU-approval gate RIGOR requires. Everything else is
automated:

  dispatch : inject parameters into the notebook (papermill-convention injected cell,
             no papermill dependency) and stage it into the local Google-Drive-for-
             Desktop synced folder -> it appears in Colab within seconds.
  poll     : wait for the notebook's results.json to sync back down, validate it, and
             append a row to the experiments journal.
  collect  : one-shot, non-blocking version of poll.

Requirements on the notebook (same conventions as run-remote; the agent applies them):
  - a `parameters`-tagged cell holding every injectable knob;
  - a final cell exporting metrics to results.json (float()-cast);
  - a Colab-persist cell that copies results.json to RIGOR_RUN_DIR when Drive is
    mounted (snippet in SKILL.md). `dispatch` injects RIGOR_RUN_DIR automatically.

Machine setup (one-time): install Google Drive for Desktop and note your synced root,
e.g.  G:/My Drive  (Windows)  ->  pass --sync-dir "G:/My Drive/rigor-runs".

Stdlib only, Python 3.10+.  Windows: set PYTHONUTF8=1.

Usage:
  python colab_run.py dispatch --notebook nb.ipynb --sync-dir "G:/My Drive/rigor-runs" \
                      --run-id exp1-seed42 --param SEED=42 --param SMOKE_TEST=False
  python colab_run.py poll    --sync-dir "G:/My Drive/rigor-runs" --run-id exp1-seed42 \
                      --timeout-min 120 [--journal experiments.md]
"""
import argparse
import ast
import datetime
import json
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DRIVE_MOUNT = "/content/drive/MyDrive"   # where Colab mounts "My Drive"


# ---------------- parameter injection (papermill convention, no papermill) ----------------

def _to_py_literal(v: str) -> str:
    """CLI 'K=V' values -> Python literal source. Parses Python literals (True/False/
    None/ints/floats/quoted strings) via ast.literal_eval; anything else -- including
    bare words like a hostname or token -- becomes a plain string. (Not json.loads:
    JSON's lowercase true/false/null would silently mis-parse Python-style True/False,
    and any resulting non-empty string is truthy regardless -- SMOKE_TEST=False would
    have injected the *string* 'False', which is truthy, silently defeating the switch.)"""
    try:
        return repr(ast.literal_eval(v))
    except Exception:
        return repr(v)


def inject_parameters(nb: dict, params: dict) -> dict:
    """Insert an `injected-parameters` cell right after the `parameters`-tagged cell
    (papermill's convention, so downstream tooling recognizes it). Fails loudly if the
    notebook has no parameters cell -- injecting blind would silently do nothing when a
    later cell reassigns the knob."""
    cells = nb.get("cells", [])
    idx = None
    for i, c in enumerate(cells):
        if "parameters" in (c.get("metadata", {}).get("tags") or []):
            idx = i
            break
    if idx is None:
        raise SystemExit("[dispatch] notebook has no `parameters`-tagged cell -- add one "
                         "(run-remote conventions) before dispatching")
    src = ["# injected by colab_run.py -- overrides the parameters cell above\n"]
    src += [f"{k} = {v}\n" for k, v in params.items()]
    cells.insert(idx + 1, {
        "cell_type": "code",
        "metadata": {"tags": ["injected-parameters"]},
        "execution_count": None,
        "outputs": [],
        "source": src,
    })
    return nb


# ---------------- journal ----------------

def append_journal(journal: str, row: dict):
    header = ("| run_id | backend | params | status | summary | finished_utc |\n"
              "|---|---|---|---|---|---|\n")
    exists = os.path.exists(journal)
    with open(journal, "a", encoding="utf-8") as f:
        if not exists:
            f.write("# Experiments journal (colab_run.py appends here)\n\n" + header)
        f.write(f"| {row['run_id']} | colab | {row['params']} | {row['status']} "
                f"| {row['summary']} | {row['finished']} |\n")


def summarize_results(d: dict) -> str:
    res = d.get("results") or {}
    parts = []
    for cfg, r in list(res.items())[:6]:
        m = r.get("mape")
        parts.append(f"{cfg}={m:.3f}" if isinstance(m, (int, float)) else f"{cfg}=?")
    more = f" (+{len(res) - 6} more)" if len(res) > 6 else ""
    smoke = " [SMOKE]" if d.get("smoke_test") else ""
    return "mape: " + ", ".join(parts) + more + smoke if parts else "no results key!"


# ---------------- commands ----------------

def cmd_dispatch(args):
    with open(args.notebook, encoding="utf-8") as f:
        nb = json.load(f)
    blob = json.dumps(nb)
    if args.results_name not in blob:
        print(f"[warn] notebook never mentions '{args.results_name}' -- does it export "
              f"results? (run-remote conventions require an export cell)")
    if "google.colab" not in blob:
        print("[warn] notebook has no Colab-persist cell (`google.colab` drive mount + "
              "copy of results.json to RIGOR_RUN_DIR) -- results will NOT sync back. "
              "See SKILL.md for the snippet.")

    params = {}
    for tok in args.param or []:
        k, _, v = tok.partition("=")
        if not _:
            raise SystemExit(f"[dispatch] bad --param '{tok}' (want K=V)")
        params[k.strip()] = _to_py_literal(v)
    # where the notebook should persist results, as seen from INSIDE Colab
    rel = args.run_id if not args.drive_subdir else f"{args.drive_subdir}/{args.run_id}"
    params["RIGOR_RUN_DIR"] = repr(f"{DRIVE_MOUNT}/{rel}")

    nb = inject_parameters(nb, params)
    run_dir = os.path.join(args.sync_dir, args.run_id)
    os.makedirs(run_dir, exist_ok=True)
    dest = os.path.join(run_dir, "notebook.ipynb")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(nb, f, ensure_ascii=False, indent=1)

    print(f"[dispatch] staged: {dest}")
    print(f"[dispatch] injected params: " + ", ".join(f"{k}={v}" for k, v in params.items()))
    print("\nNext (the one human step -- this tap IS the GPU approval):")
    print("  1. Wait a few seconds for Drive to sync the folder up.")
    print("  2. Open https://colab.research.google.com -> Open notebook -> Google Drive")
    print(f"     -> {rel}/notebook.ipynb   (works from a phone too)")
    print("  3. Runtime -> Change runtime type -> pick the GPU -> Runtime -> Run all")
    print("     (approve the Drive-mount prompt when the notebook asks).")
    print(f"\nThen:  python colab_run.py poll --sync-dir \"{args.sync_dir}\" "
          f"--run-id {args.run_id}")


def _check_once(args):
    path = os.path.join(args.sync_dir, args.run_id, args.results_name)
    if not os.path.exists(path):
        return None, path
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), path
    except json.JSONDecodeError:
        return "partial", path   # Drive may still be syncing the file down


def _finish(args, data, path):
    summary = summarize_results(data)
    print(f"[poll] results landed: {path}")
    print(f"[poll] {summary}")
    append_journal(args.journal, {
        "run_id": args.run_id, "params": "(see injected cell)", "status": "complete",
        "summary": summary,
        "finished": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    })
    print(f"[poll] journal row appended -> {args.journal}")
    print("[poll] now run the /verify-run checklist against it before reporting numbers.")


def cmd_poll(args):
    deadline = time.time() + args.timeout_min * 60
    print(f"[poll] waiting for {args.results_name} (timeout {args.timeout_min} min) ...")
    while time.time() < deadline:
        data, path = _check_once(args)
        if isinstance(data, dict):
            return _finish(args, data, path)
        if data == "partial":
            print("  [..] file syncing (invalid JSON right now), retrying ...")
        time.sleep(args.interval)
    raise SystemExit(f"[poll] TIMEOUT after {args.timeout_min} min -- check the Colab tab "
                     f"(session evicted? error in a cell?) and the Drive sync status.")


def cmd_collect(args):
    data, path = _check_once(args)
    if isinstance(data, dict):
        return _finish(args, data, path)
    raise SystemExit(f"[collect] no valid {args.results_name} at {path} yet")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("dispatch")
    d.add_argument("--notebook", required=True)
    d.add_argument("--sync-dir", required=True, help="local Drive-synced runs folder")
    d.add_argument("--run-id", required=True)
    d.add_argument("--param", action="append", help="K=V (repeatable)")
    d.add_argument("--drive-subdir", default="rigor-runs",
                   help="path of --sync-dir relative to the Drive root (for the "
                        "in-Colab RIGOR_RUN_DIR); default rigor-runs")
    d.add_argument("--results-name", default="results.json")
    d.set_defaults(fn=cmd_dispatch)

    for name, fn in (("poll", cmd_poll), ("collect", cmd_collect)):
        p = sub.add_parser(name)
        p.add_argument("--sync-dir", required=True)
        p.add_argument("--run-id", required=True)
        p.add_argument("--results-name", default="results.json")
        p.add_argument("--journal", default="experiments.md")
        if name == "poll":
            p.add_argument("--timeout-min", type=int, default=180)
            p.add_argument("--interval", type=int, default=30)
        p.set_defaults(fn=fn)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

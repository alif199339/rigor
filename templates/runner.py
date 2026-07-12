"""
Kaggle Agent runner -- papermill-inject -> kaggle push -> poll -> download -> parse -> log.

Architecture:

    sweep.yaml (WHAT to run -- defined by the researcher, never the agent)
          |
          v
    runner.py (HOW: for each run in <sweep>.runs)
          1. papermill.execute_notebook(..., prepare_only=True)
                 -> runs/<run_id>/notebook.ipynb (params injected, NOT executed locally)
          2. write runs/<run_id>/kernel-metadata.json
          3. `kaggle kernels push -p runs/<run_id>`        -> executes on Kaggle's CPU/GPU
          4. poll `kaggle kernels status <id>` until complete/error/timeout
          5. `kaggle kernels output <id> -p runs/<run_id>/output/`
                 -> pulls back whatever the notebook wrote (results.json, figures/) plus a
                    plain-text kernel log. CONFIRMED (2026-07-05): unlike Colab, Kaggle's
                    `kernels output` never returns the executed .ipynb with output cells --
                    only the working-directory artifacts and the log. So the source notebook
                    in Notebook Codes/ stays as the clean parameterized template; results.json
                    (in runs/<run_id>/output/) plus experiments.md are the record of a run.
          6. parse output/results.json
          7. append one row to experiments.md   (cross-process lock -- concurrent accounts)
          8. on failure: save the raw Kaggle output/log to logs/<run_id>_error.log, record the
             failure, and continue to the next run -- one bad run must not kill the sweep

Multi-account (verified 2026-07-05): the account switch is the KAGGLE_API_TOKEN env var,
NOT KAGGLE_CONFIG_DIR (the newer KGAT_ bearer scheme silently ignores KAGGLE_CONFIG_DIR
and falls back to ~/.kaggle/access_token). accounts.yaml maps a name -> {kaggle_username,
token_file}; --account NAME reads that token file and injects it as KAGGLE_API_TOKEN for
every kaggle CLI call. Account 1 (default ~/.kaggle/access_token) is used when --account is
omitted. Run different sweeps on different accounts concurrently by launching several
`runner.py --account X --sweep Y` processes; the append lock keeps experiments.md intact.
A wrong-account token can't silently corrupt data: the kernel is pushed under the account's
own username and reads that account's own private dataset, so a mismatched token fails the
push rather than writing to the wrong place.

Usage:
    py -3.11 runner.py                               # run every entry in sweep.yaml (default account)
    py -3.11 runner.py --sweep sweep_experiment2.yaml --account acct2
    py -3.11 runner.py --dry-run                     # steps 1-2 only, no Kaggle push
    py -3.11 runner.py --max-runs 1                  # cap how many runs execute this invocation
    py -3.11 runner.py --resume                      # skip run_ids already marked complete
    py -3.11 runner.py --account acct2 --check       # auth smoke-test: `datasets list --mine`, then exit
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

# papermill is imported lazily inside prepare_run() -- it is heavy and only needed to
# stage a notebook, so the pure helpers (journal append/dedup, results parsing) stay
# importable, and testable offline, without it.

# The kaggle CLI reads notebook files with the platform default encoding unless
# Python's UTF-8 mode is on; on Windows that default is cp1252, which crashes on
# any real Unicode character in the notebook (arrows, em-dashes, etc.). PYTHONUTF8=1
# (PEP 540) forces UTF-8 for all text I/O in the subprocess, independent of locale.
# Filled in by configure_env(); KAGGLE_API_TOKEN is added when an account is selected.
_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

AGENT_DIR = Path(__file__).resolve().parent
RUNS_DIR = AGENT_DIR / "runs"
LOGS_DIR = AGENT_DIR / "logs"
EXPERIMENTS_MD = AGENT_DIR / "experiments.md"
EXPERIMENTS_LOCK = AGENT_DIR / "experiments.md.lock"
ACCOUNTS_YAML = AGENT_DIR / "accounts.yaml"

# Matches both bare ids (run001) and prefixed ids (myproj-exp1-run001).
RUN_ROW_RE = re.compile(r"^\|\s*([\w-]*run\d+)\s*\|")


def configure_env(token: str | None):
    """Set the subprocess env for all kaggle() calls. token=None -> default account 1."""
    global _ENV
    _ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    if token:
        _ENV["KAGGLE_API_TOKEN"] = token


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_accounts() -> dict:
    if not ACCOUNTS_YAML.exists():
        return {}
    data = load_yaml(ACCOUNTS_YAML) or {}
    return {a["name"]: a for a in data.get("accounts", [])}


def resolve_account(name: str | None) -> dict | None:
    """Return {name, kaggle_username, token} for --account, or None for the default account."""
    if not name:
        return None
    accounts = load_accounts()
    if name not in accounts:
        sys.exit(f"[fatal] account '{name}' not found in {ACCOUNTS_YAML.name}. "
                 f"Known: {sorted(accounts)}")
    acct = dict(accounts[name])
    tf = acct.get("token_file")
    if tf:
        tok_path = Path(tf)
        if not tok_path.exists():
            sys.exit(f"[fatal] token file for '{name}' not found: {tf}")
        acct["token"] = tok_path.read_text(encoding="utf-8").strip()
    else:
        acct["token"] = None  # default ~/.kaggle/access_token
    return acct


# ---- cross-process lock for experiments.md (concurrent per-account runners) ----

def _acquire_lock(timeout: float = 30.0):
    start = time.time()
    while True:
        try:
            fd = os.open(str(EXPERIMENTS_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return
        except FileExistsError:
            if time.time() - start > timeout:
                # Assume a stale lock (a crashed runner) and proceed rather than hang.
                print("  [warn] experiments.md lock stale >30s -- proceeding")
                return
            time.sleep(0.15)


def _release_lock():
    try:
        os.remove(str(EXPERIMENTS_LOCK))
    except FileNotFoundError:
        pass


def completed_run_ids() -> set:
    if not EXPERIMENTS_MD.exists():
        return set()
    ids = set()
    for line in EXPERIMENTS_MD.read_text(encoding="utf-8").splitlines():
        m = RUN_ROW_RE.match(line)
        if m and "complete" in line:
            ids.add(m.group(1))
    return ids


def append_experiment_row(row: dict):
    header = "| run_id | account | params | status | mape_summary | runtime_s | timestamp |\n"
    sep = "|---|---|---|---|---|---|---|\n"
    line = (f"| {row['run_id']} | {row.get('account', '-')} | {row['params']} | "
            f"{row['status']} | {row['mape_summary']} | {row['runtime_s']:.0f} | "
            f"{row['timestamp']} |\n")
    _acquire_lock()
    try:
        if not EXPERIMENTS_MD.exists():
            EXPERIMENTS_MD.write_text("# Experiments\n\n" + header + sep, encoding="utf-8")
        with open(EXPERIMENTS_MD, "a", encoding="utf-8") as f:
            f.write(line)
    finally:
        _release_lock()


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def summarize_mape(parsed: dict | None) -> str:
    """One-line 'cfg=MAPE%' summary for the experiments journal. Casts every value
    through float() so numpy scalars -- which json.dump(..., default=str) would silently
    stringify -- format numerically, and skips any config with no usable mape."""
    if not parsed or "results" not in parsed:
        return "-"
    parts = []
    for k, v in (parsed.get("results") or {}).items():
        m = (v or {}).get("mape")
        if m is None:
            continue
        try:
            parts.append(f"{k}={float(m):.2f}%")
        except (TypeError, ValueError):
            continue
    return ", ".join(parts) if parts else "-"


def kaggle(*args, check=True):
    """Invoke the kaggle CLI via THIS interpreter (avoids PATH ambiguity on Windows)."""
    cmd = [sys.executable, "-m", "kaggle", *args]
    print("  $", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=check, env=_ENV)


def run_id_for(sweep: dict, i: int) -> str:
    """Namespaced by the sweep's kernel_slug_prefix so different sweeps never collide
    in runs/ or experiments.md. The Kaggle kernel slug == run_id (prefix already inside)."""
    return f"{sweep['kernel_slug_prefix']}-run{i:03d}"


def prepare_run(sweep: dict, run_id: str, params: dict, username: str) -> Path:
    import papermill as pm  # lazy: heavy, and only needed here (keeps helpers testable)

    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    src_notebook = (AGENT_DIR / sweep["notebook"]).resolve()
    out_notebook = run_dir / "notebook.ipynb"

    pm.execute_notebook(
        str(src_notebook), str(out_notebook),
        parameters=params, prepare_only=True,
    )
    print(f"  [prepared] {out_notebook} (params injected, not executed locally)")

    kernel_id = f"{username}/{run_id}"
    meta = {
        "id": kernel_id,
        "title": run_id,
        "code_file": "notebook.ipynb",
        "language": "python",
        "kernel_type": "notebook",
        "is_private": True,
        "enable_gpu": bool(sweep.get("enable_gpu", False)),
        "enable_internet": bool(sweep.get("enable_internet", True)),
        # Kaggle's default P100 image currently ships a PyTorch build with no sm_60
        # (Pascal) kernels -- crashes on first CUDA op ("no kernel image is available
        # for execution on the device"). Pin T4 explicitly to avoid it (confirmed bug,
        # see Kaggle/docker-python#1546). Empty string = let Kaggle pick (old default).
        "machine_shape": sweep.get("machine_shape", "NvidiaTeslaT4") if sweep.get("enable_gpu", False) else "",
        "dataset_sources": sweep.get("dataset_sources", []),
        "competition_sources": [],
        "kernel_sources": [],
    }
    (run_dir / "kernel-metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"  [kernel-metadata] id={kernel_id} enable_gpu={meta['enable_gpu']} "
          f"machine={meta['machine_shape'] or '(default)'}")
    return run_dir


def push_and_wait(run_dir: Path, kernel_id: str, poll_interval: int, max_minutes: int) -> str:
    result = kaggle("kernels", "push", "-p", str(run_dir), check=False)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return "push_error"

    deadline = time.time() + max_minutes * 60
    while time.time() < deadline:
        time.sleep(poll_interval)
        status_res = kaggle("kernels", "status", kernel_id, check=False)
        status_text = (status_res.stdout or status_res.stderr or "").strip()
        print(f"  [poll] {status_text}")
        low = status_text.lower()
        if "complete" in low:
            return "complete"
        if "error" in low or "cancel" in low:
            return "error"
    return "timeout"


def download_and_parse(run_dir: Path, kernel_id: str) -> dict:
    out_dir = run_dir / "output"
    out_dir.mkdir(exist_ok=True)
    result = kaggle("kernels", "output", kernel_id, "-p", str(out_dir), check=False)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)

    results_path = out_dir / "results.json"
    parsed = None
    if results_path.exists():
        parsed = json.loads(results_path.read_text(encoding="utf-8"))
    else:
        print("  [warn] results.json not found in Kaggle output")

    files = sorted(p.name for p in out_dir.rglob("*") if p.is_file())
    print(f"  [output files] {files}")
    return {"parsed": parsed, "files": files, "out_dir": out_dir}


def run_one(sweep: dict, run_id: str, params: dict, username: str, acct_name: str, dry_run: bool):
    print(f"\n=== {run_id}  account={acct_name}  params={params}  dry_run={dry_run} ===")
    t0 = time.time()
    run_dir = prepare_run(sweep, run_id, params, username)
    kernel_id = f"{username}/{run_id}"

    if dry_run:
        append_experiment_row(dict(
            run_id=run_id, account=acct_name, params=params, status="dry_run",
            mape_summary="-", runtime_s=time.time() - t0, timestamp=now_iso(),
        ))
        return

    status = push_and_wait(run_dir, kernel_id,
                           sweep.get("poll_interval_seconds", 60),
                           sweep.get("max_poll_minutes", 120))
    dl = download_and_parse(run_dir, kernel_id)

    if status != "complete":
        LOGS_DIR.mkdir(exist_ok=True)
        log_path = LOGS_DIR / f"{run_id}_error.log"
        log_path.write_text(json.dumps(dl, default=str, indent=2), encoding="utf-8")
        append_experiment_row(dict(
            run_id=run_id, account=acct_name, params=params, status=f"FAILED ({status})",
            mape_summary="-", runtime_s=time.time() - t0, timestamp=now_iso(),
        ))
        print(f"  [FAILED] see {log_path}")
        return

    mape_summary = summarize_mape(dl["parsed"])

    append_experiment_row(dict(
        run_id=run_id, account=acct_name, params=params, status="complete",
        mape_summary=mape_summary, runtime_s=time.time() - t0, timestamp=now_iso(),
    ))

    if params.get("SMOKE_TEST", False):
        print("  [note] SMOKE_TEST run -- MAPE numbers above are meaningless (pipeline check only).")

    print(f"  [{run_id}] done in {time.time() - t0:.0f}s -> {mape_summary}")
    print(f"  [record] full results: {dl['out_dir'] / 'results.json'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", default="sweep.yaml", help="sweep YAML file (in this directory)")
    ap.add_argument("--account", default=None, help="account name from accounts.yaml (default: account 1)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-runs", type=int, default=None)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="auth smoke-test: run `kaggle datasets list --mine` for the account and exit")
    args = ap.parse_args()

    account = resolve_account(args.account)
    configure_env(account["token"] if account else None)
    acct_name = args.account or "acct1(default)"

    if args.check:
        print(f"[check] account={acct_name} "
              f"username={account['kaggle_username'] if account else '(default ~/.kaggle)'}")
        res = kaggle("datasets", "list", "--mine", check=False)
        print(res.stdout or res.stderr)
        return

    sweep_path = AGENT_DIR / args.sweep
    if not sweep_path.exists():
        sys.exit(f"[fatal] sweep file not found: {sweep_path}")
    sweep = load_yaml(sweep_path)

    # Username comes from the account when given, else the sweep file.
    username = account["kaggle_username"] if account else sweep.get("kaggle_username")
    if not username:
        sys.exit("[fatal] no kaggle_username: pass --account or set it in the sweep file")

    # Soft guard: private dataset slugs must belong to the pushing account, or the push
    # will fail to access them. Warn early rather than after a wasted push.
    for slug in sweep.get("dataset_sources", []):
        if "/" in slug and not slug.startswith(username + "/"):
            print(f"  [warn] dataset '{slug}' is not under '{username}' -- "
                  f"if it's private this push will fail; update dataset_sources for this account.")

    RUNS_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)

    done = completed_run_ids() if args.resume else set()

    count = 0
    for i, run_cfg in enumerate(sweep["runs"], start=1):
        if args.max_runs is not None and count >= args.max_runs:
            break
        run_id = run_id_for(sweep, i)
        if run_id in done:
            print(f"[skip] {run_id} already complete (--resume)")
            continue
        run_one(sweep, run_id, run_cfg, username, acct_name, args.dry_run)
        count += 1


if __name__ == "__main__":
    main()

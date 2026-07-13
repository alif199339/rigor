"""data_audit.py -- dataset fingerprinting, degeneracy detection, and drift checks.

RIGOR skill: /data-audit. Stdlib-only (CSV core; .npy needs numpy), Python 3.10+.

The failure this guards against: a dataset or preprocessed bundle silently stops
meaning what you think it means -- a feature column goes all-constant after a merge
bug, nulls creep in, a rebuild changes shape -- and every downstream result quietly
inherits it. The audit is mechanical and two-phase:

  fingerprint <path> [--out fp.json]     record what the data IS right now
  verify      <path> --against fp.json   recompute and diff; exit 1 on hard drift

Degeneracy checks run in BOTH phases (a first fingerprint of already-broken data
should shout too): constant columns, all-null columns, high-null columns,
duplicate column pairs. `<path>` = a CSV file, an .npy file, or a directory
(every .csv/.npy/.json inside, non-recursive by default; --recursive to walk).

Everything is reported; nothing is ever modified.
"""
import argparse
import csv
import glob
import hashlib
import json
import math
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NULLS = {"", "na", "nan", "null", "none", "n/a"}


def sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ---------------- per-format fingerprints ----------------

def fp_csv(path, max_distinct=1000):
    cols, rows = [], 0
    stats = []  # per column: {nulls, distinct(set|None), min, max, sum, numeric, colhash}
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f)
        try:
            cols = next(reader)
        except StopIteration:
            return {"format": "csv", "rows": 0, "columns": [], "column_stats": {}}
        stats = [{"nulls": 0, "distinct": set(), "min": None, "max": None,
                  "numeric": 0, "h": hashlib.sha256()} for _ in cols]
        for row in reader:
            rows += 1
            for i in range(len(cols)):
                v = row[i].strip() if i < len(row) else ""
                st = stats[i]
                st["h"].update(v.encode("utf-8", "replace") + b"\x1f")
                if v.lower() in NULLS:
                    st["nulls"] += 1
                    continue
                if st["distinct"] is not None:
                    st["distinct"].add(v)
                    if len(st["distinct"]) > max_distinct:
                        st["distinct"] = None          # too many to track; not constant
                try:
                    x = float(v)
                    if not math.isnan(x):
                        st["numeric"] += 1
                        st["min"] = x if st["min"] is None else min(st["min"], x)
                        st["max"] = x if st["max"] is None else max(st["max"], x)
                except ValueError:
                    pass
    col_stats = {}
    for c, st in zip(cols, stats):
        nd = len(st["distinct"]) if st["distinct"] is not None else None
        col_stats[c] = {"nulls": st["nulls"],
                        "distinct": nd,
                        "min": st["min"], "max": st["max"],
                        "col_hash": st["h"].hexdigest()[:16]}
    return {"format": "csv", "rows": rows, "columns": cols, "column_stats": col_stats}


def fp_npy(path):
    try:
        import numpy as np
    except ImportError:
        return {"format": "npy", "note": "numpy not installed -- file-hash only"}
    a = np.load(path, allow_pickle=False, mmap_mode="r")
    a64 = np.asarray(a, dtype="float64") if a.dtype.kind in "fiu" else None
    out = {"format": "npy", "shape": list(a.shape), "dtype": str(a.dtype)}
    if a64 is not None and a64.size:
        out["nan_count"] = int(np.isnan(a64).sum())
        finite = a64[~np.isnan(a64)]
        if finite.size:
            out["min"] = float(finite.min())
            out["max"] = float(finite.max())
            out["constant"] = bool(finite.min() == finite.max())
    return out


def fingerprint_file(path):
    ext = os.path.splitext(path)[1].lower()
    fp = {"file": os.path.basename(path), "bytes": os.path.getsize(path),
          "sha256": sha256(path)}
    if ext == ".csv":
        fp.update(fp_csv(path))
    elif ext == ".npy":
        fp.update(fp_npy(path))
    else:
        fp["format"] = ext.lstrip(".") or "raw"
    return fp


# ---------------- degeneracy ----------------

def degeneracies(fp):
    """Warnings a data file should never silently carry (the all-zero-column class)."""
    warn = []
    rows = fp.get("rows", 0)
    for c, st in (fp.get("column_stats") or {}).items():
        nn = rows - st["nulls"]
        if rows and st["nulls"] == rows:
            warn.append(f"column '{c}' is ALL-NULL")
        elif rows and st["nulls"] > 0.5 * rows:
            warn.append(f"column '{c}' is {100 * st['nulls'] / rows:.0f}% null")
        elif nn > 1 and st.get("distinct") == 1:
            warn.append(f"column '{c}' is CONSTANT over all {nn} non-null rows")
    by_hash = {}
    for c, st in (fp.get("column_stats") or {}).items():
        by_hash.setdefault(st["col_hash"], []).append(c)
    for h, cs in by_hash.items():
        if len(cs) > 1:
            warn.append(f"columns {cs} are byte-identical DUPLICATES")
    if fp.get("format") == "npy":
        if fp.get("constant"):
            warn.append("array is CONSTANT")
        if fp.get("nan_count"):
            warn.append(f"array contains {fp['nan_count']} NaN(s)")
    return warn


# ---------------- drift ----------------

def drift(old, new):
    """(hard, soft) differences between two fingerprints of the same file."""
    hard, soft = [], []
    if old.get("sha256") == new.get("sha256"):
        return hard, soft                       # byte-identical: nothing to say
    soft.append("content hash changed")
    for k in ("rows", "shape", "dtype"):
        if old.get(k) != new.get(k) and (k in old or k in new):
            hard.append(f"{k}: {old.get(k)} -> {new.get(k)}")
    oc, nc = old.get("columns"), new.get("columns")
    if oc is not None and nc is not None and oc != nc:
        gone, added = set(oc) - set(nc), set(nc) - set(oc)
        hard.append(f"columns changed: -{sorted(gone)} +{sorted(added)}"
                    if gone or added else "column ORDER changed")
    os_, ns = old.get("column_stats") or {}, new.get("column_stats") or {}
    for c in set(os_) & set(ns):
        o, n = os_[c], ns[c]
        if o["nulls"] != n["nulls"]:
            (hard if n["nulls"] > o["nulls"] else soft).append(
                f"column '{c}' nulls: {o['nulls']} -> {n['nulls']}")
        if o.get("distinct") and o["distinct"] > 1 and n.get("distinct") == 1:
            hard.append(f"column '{c}' COLLAPSED to constant")
    if old.get("nan_count") is not None and new.get("nan_count", 0) > old["nan_count"]:
        hard.append(f"nan_count: {old['nan_count']} -> {new['nan_count']}")
    return hard, soft


# ---------------- commands ----------------

def collect(path, recursive):
    if os.path.isfile(path):
        return [path]
    pat = "**/*" if recursive else "*"
    return sorted(p for p in glob.glob(os.path.join(path, pat), recursive=recursive)
                  if os.path.isfile(p)
                  and os.path.splitext(p)[1].lower() in (".csv", ".npy", ".json"))


def cmd_fingerprint(args):
    files = collect(args.path, args.recursive)
    if not files:
        raise SystemExit(f"[data-audit] nothing to fingerprint at {args.path}")
    fps, n_warn = {}, 0
    for p in files:
        fp = fingerprint_file(p)
        warns = degeneracies(fp)
        fp["degeneracies"] = warns
        rel = os.path.relpath(p, args.path) if os.path.isdir(args.path) else \
            os.path.basename(p)
        fps[rel.replace(os.sep, "/")] = fp
        shape = fp.get("rows", fp.get("shape", "?"))
        print(f"  [fp] {rel}  ({fp['format']}, {shape} rows/shape, "
              f"{fp['bytes']:,} B)")
        for w in warns:
            n_warn += 1
            print(f"       [!] {w}")
    out = args.out or (os.path.join(args.path, "fingerprint.json")
                       if os.path.isdir(args.path) else args.path + ".fp.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(fps, f, indent=1)
    print(f"[data-audit] fingerprinted {len(fps)} file(s) -> {out}"
          + (f"  ({n_warn} degeneracy warning(s) ABOVE)" if n_warn else ""))
    sys.exit(2 if n_warn and args.strict else 0)


def cmd_verify(args):
    with open(args.against, encoding="utf-8") as f:
        old_all = json.load(f)
    files = collect(args.path, args.recursive)
    new_all = {}
    for p in files:
        rel = os.path.relpath(p, args.path) if os.path.isdir(args.path) else \
            os.path.basename(p)
        new_all[rel.replace(os.sep, "/")] = fingerprint_file(p)
    failures = 0
    for rel in sorted(set(old_all) | set(new_all)):
        if rel not in new_all:
            print(f"  [FAIL] {rel}: file MISSING (was fingerprinted)")
            failures += 1
            continue
        if rel not in old_all:
            print(f"  [warn] {rel}: new file, not in the fingerprint")
            continue
        hard, soft = drift(old_all[rel], new_all[rel])
        warns = degeneracies(new_all[rel])
        if not hard and not soft and not warns:
            print(f"  [ok]   {rel}: byte-identical")
            continue
        for h in hard:
            print(f"  [FAIL] {rel}: {h}")
            failures += 1
        for s in soft:
            print(f"  [warn] {rel}: {s}")
        for w in warns:
            print(f"  [FAIL] {rel}: degeneracy: {w}")
            failures += 1
    print(f"[data-audit] verify: {failures} hard failure(s) across {len(new_all)} file(s)")
    sys.exit(1 if failures else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fingerprint")
    p.add_argument("path")
    p.add_argument("--out", help="default: <dir>/fingerprint.json or <file>.fp.json")
    p.add_argument("--recursive", action="store_true")
    p.add_argument("--strict", action="store_true",
                   help="exit 2 if the fresh fingerprint already carries degeneracies")
    p = sub.add_parser("verify")
    p.add_argument("path")
    p.add_argument("--against", required=True, help="fingerprint.json to compare with")
    p.add_argument("--recursive", action="store_true")
    args = ap.parse_args()
    {"fingerprint": cmd_fingerprint, "verify": cmd_verify}[args.cmd](args)


if __name__ == "__main__":
    main()

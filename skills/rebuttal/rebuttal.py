"""rebuttal.py -- track reviewer comments -> responses -> verified manuscript changes.

RIGOR skill: /rebuttal. Stdlib-only, Python 3.10+.

The classic rebuttal failure: the response letter says "we have revised Section 3
accordingly" and the manuscript diff shows nothing. This tool makes every
change-claim mechanically checkable:

  init      --dir D                          create the comment store
  import    --dir D --file reviews.txt [--reviewer R1]   split a review into comments
  add       --dir D --reviewer R2 --text "..."           one comment by hand
  respond   --dir D <comment-id> --text "..."
            [--action change|clarify|decline]
            [--anchors file1,file2] [--quote "text now in the manuscript"]
  status    --dir D                          the checklist (what's still open)
  check     --dir D --diff-file changes.patch [--manuscript main.tex]
            verify every change-claiming response against the actual diff
  compile   --dir D [--out RESPONSE.md]      the response letter, grouped by reviewer

`check` logic, per response with action=change:
  - every --anchors file must appear in the diff (else CLAIMED-BUT-NO-DIFF)
  - a --quote must appear in the diff's ADDED lines or in --manuscript's current
    text (else QUOTE-NOT-FOUND)
Exit 1 if any change-claim fails or any comment is unanswered.
"""
import argparse
import json
import os
import re
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ACTIONS = ("change", "clarify", "decline")


def store_path(d):
    return os.path.join(d, "comments.json")


def load(d):
    p = store_path(d)
    if not os.path.exists(p):
        raise SystemExit(f"[rebuttal] no store at {p} -- run `init` first")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(d, data):
    with open(store_path(d), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def cmd_init(args):
    os.makedirs(args.dir, exist_ok=True)
    if os.path.exists(store_path(args.dir)):
        raise SystemExit(f"[rebuttal] {store_path(args.dir)} already exists")
    save(args.dir, {"created": time.strftime("%Y-%m-%d"), "comments": []})
    print(f"[ok] rebuttal store at {store_path(args.dir)}")


def next_id(data, reviewer):
    n = sum(1 for c in data["comments"] if c["reviewer"] == reviewer) + 1
    return f"{reviewer}.{n}"


def cmd_import(args):
    data = load(args.dir)
    text = open(args.file, encoding="utf-8").read()
    # split on numbered items ("1.", "2)", "C3:") or blank-line paragraphs
    parts = re.split(r"\n\s*(?=(?:\d+[.):]|[A-Z]\d+[.:]))", text)
    if len(parts) < 2:
        parts = [p for p in re.split(r"\n\s*\n", text)]
    added = 0
    for p in parts:
        p = re.sub(r"\s+", " ", p).strip()
        if len(p) < 15:                      # skip greetings/fragments
            continue
        cid = next_id(data, args.reviewer)
        data["comments"].append({"id": cid, "reviewer": args.reviewer, "text": p,
                                 "response": None, "action": None,
                                 "anchors": [], "quote": None})
        added += 1
    save(args.dir, data)
    print(f"[ok] imported {added} comment(s) as {args.reviewer}.* "
          f"(store: {len(data['comments'])})")


def cmd_add(args):
    data = load(args.dir)
    cid = next_id(data, args.reviewer)
    data["comments"].append({"id": cid, "reviewer": args.reviewer, "text": args.text,
                             "response": None, "action": None, "anchors": [],
                             "quote": None})
    save(args.dir, data)
    print(f"[ok] {cid}: {args.text[:70]}")


def find(data, cid):
    for c in data["comments"]:
        if c["id"] == cid:
            return c
    raise SystemExit(f"[rebuttal] unknown comment '{cid}' "
                     f"(have: {', '.join(c['id'] for c in data['comments'])})")


def cmd_respond(args):
    data = load(args.dir)
    c = find(data, args.id)
    if args.action and args.action not in ACTIONS:
        raise SystemExit(f"[rebuttal] --action must be one of {ACTIONS}")
    c["response"] = args.text
    c["action"] = args.action or ("change" if (args.anchors or args.quote)
                                  else "clarify")
    c["anchors"] = [a.strip() for a in (args.anchors or "").split(",") if a.strip()]
    c["quote"] = args.quote
    if c["action"] == "change" and not c["anchors"] and not c["quote"]:
        print("[warn] a `change` response with no --anchors/--quote cannot be "
              "verified by `check` -- add the file(s) you changed")
    save(args.dir, data)
    print(f"[ok] {c['id']} answered ({c['action']})")


def cmd_status(args):
    data = load(args.dir)
    open_, answered = [], []
    for c in data["comments"]:
        (answered if c["response"] else open_).append(c)
    print(f"== rebuttal: {len(data['comments'])} comments, "
          f"{len(open_)} open, {len(answered)} answered ==")
    for c in data["comments"]:
        state = ("OPEN     " if not c["response"] else
                 f"{c['action']:9s}")
        anch = f"  anchors: {','.join(c['anchors'])}" if c["anchors"] else ""
        print(f"{state} {c['id']:7s} {c['text'][:70]}{anch}")
    if open_:
        print(f"-- {len(open_)} comment(s) still need a response --")


def parse_diff(path):
    """(files_touched, added_text) from a unified diff."""
    files, added = set(), []
    for line in open(path, encoding="utf-8", errors="replace"):
        m = re.match(r"\+\+\+ (?:b/)?(.+)", line)
        if m and m.group(1) != "/dev/null":
            files.add(m.group(1).strip())
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
    return files, re.sub(r"\s+", " ", "".join(added))


def cmd_check(args):
    data = load(args.dir)
    files, added = parse_diff(args.diff_file)
    manuscript = ""
    if args.manuscript and os.path.exists(args.manuscript):
        manuscript = re.sub(r"\s+", " ",
                            open(args.manuscript, encoding="utf-8").read())
    failures = 0
    for c in data["comments"]:
        if not c["response"]:
            print(f"  [FAIL] {c['id']}: UNANSWERED")
            failures += 1
            continue
        if c["action"] != "change":
            print(f"  [ok]   {c['id']}: {c['action']} (no manuscript change claimed)")
            continue
        probs = []
        for a in c["anchors"]:
            base = os.path.basename(a)
            if not any(base == os.path.basename(f) or f.endswith(a) for f in files):
                probs.append(f"anchor '{a}' NOT in the diff")
        if c["quote"]:
            q = re.sub(r"\s+", " ", c["quote"]).strip()
            if q not in added and q not in manuscript:
                probs.append("quote not found in added lines or the manuscript")
        if not c["anchors"] and not c["quote"]:
            probs.append("change claimed but nothing verifiable was recorded")
        if probs:
            failures += 1
            print(f"  [FAIL] {c['id']}: CLAIMED-BUT-NOT-VERIFIED -- " + "; ".join(probs))
        else:
            print(f"  [ok]   {c['id']}: change verified "
                  f"({', '.join(c['anchors']) or 'quote'})")
    print(f"[rebuttal] check: {failures} failure(s) across {len(data['comments'])} comments")
    sys.exit(1 if failures else 0)


def cmd_compile(args):
    data = load(args.dir)
    by_rev = {}
    for c in data["comments"]:
        by_rev.setdefault(c["reviewer"], []).append(c)
    L = ["# Response to Reviewers", "",
         f"*{len(data['comments'])} comments. Every response claiming a manuscript "
         f"change is mechanically verified against the revision diff "
         f"(`rebuttal.py check`).*", ""]
    for rev in sorted(by_rev):
        L.append(f"## Reviewer {rev}")
        L.append("")
        for c in by_rev[rev]:
            L.append(f"> **{c['id']}:** {c['text']}")
            L.append("")
            if c["response"]:
                L.append(c["response"])
                if c["anchors"]:
                    L.append(f"*(changed: {', '.join(c['anchors'])})*")
            else:
                L.append("**[RESPONSE PENDING]**")
            L.append("")
    out = args.out or os.path.join(args.dir, "RESPONSE.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"[ok] compiled -> {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="rebuttal directory (one per review round)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    p = sub.add_parser("import")
    p.add_argument("--file", required=True)
    p.add_argument("--reviewer", default="R1")
    p = sub.add_parser("add")
    p.add_argument("--reviewer", required=True)
    p.add_argument("--text", required=True)
    p = sub.add_parser("respond")
    p.add_argument("id")
    p.add_argument("--text", required=True)
    p.add_argument("--action", choices=ACTIONS)
    p.add_argument("--anchors", help="comma-separated files claimed changed")
    p.add_argument("--quote", help="text now present in the manuscript")
    sub.add_parser("status")
    p = sub.add_parser("check")
    p.add_argument("--diff-file", required=True,
                   help="unified diff of the revision (e.g. `git diff old..new > d.patch`)")
    p.add_argument("--manuscript", help="current manuscript (fallback for --quote)")
    p = sub.add_parser("compile")
    p.add_argument("--out")
    args = ap.parse_args()
    {"init": cmd_init, "import": cmd_import, "add": cmd_add, "respond": cmd_respond,
     "status": cmd_status, "check": cmd_check, "compile": cmd_compile}[args.cmd](args)


if __name__ == "__main__":
    main()

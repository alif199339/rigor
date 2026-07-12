"""
topic_watch.py -- re-run a literature collection's own queries and diff for NEW papers.

Manual mode only (the scheduled/weekly version needs an always-on machine, which is a
separate, user-gated decision -- this script never schedules anything).

It reads a literature/<slug>/ collection built by the lit-review skill, recovers the
topic queries recorded in each paper's `_sources` tags (the `search:` and `bulk:` ones),
re-runs them against Semantic Scholar biased toward recent years, and writes
`watch_<date>.md` listing only the paperIds not already in the store. By default it
REPORTS only; pass --merge to also fold the new papers into papers.json.

Reuses the lit-review client (http_get, merge, store I/O) rather than duplicating it, so
it inherits the same rate-limiting, key handling, and provenance model. Requires the
sibling `.claude/skills/lit-review/lit_search.py`.

Windows: set PYTHONUTF8=1. S2 key picked up from S2_API_KEY automatically.

Usage:
  py -3.11 topic_watch.py --out literature/<slug>                 # report new papers
  py -3.11 topic_watch.py --out literature/<slug> --since-year 2025 --merge
"""
import argparse
import datetime
import os
import sys
import time
import urllib.parse

# import the lit-review client from the sibling skill folder
_LIT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lit-review")
sys.path.insert(0, _LIT)
try:
    import lit_search as L
except ImportError:
    raise SystemExit("[topic-watch] cannot import lit_search.py -- the lit-review skill "
                     "must sit alongside this one at .claude/skills/lit-review/")


def recover_queries(store):
    """query string -> the mode it was first found by (search|bulk)."""
    queries = {}
    for p in store.values():
        for s in p.get("_sources", []):
            if s.startswith("search:"):
                queries.setdefault(s[len("search:"):], "search")
            elif s.startswith("bulk:"):
                queries.setdefault(s[len("bulk:"):], "bulk")
    return queries


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="literature/<slug> collection folder")
    ap.add_argument("--since-year", type=int, default=None,
                    help="only surface papers from this year onward (default: last year)")
    ap.add_argument("--limit", type=int, default=30, help="results per query to scan")
    ap.add_argument("--merge", action="store_true", help="also add new papers to papers.json")
    args = ap.parse_args()

    store = L.load_store(args.out)
    if not store:
        raise SystemExit(f"[topic-watch] no papers.json in {args.out} -- nothing to watch")
    queries = recover_queries(store)
    if not queries:
        raise SystemExit("[topic-watch] no search/bulk queries recorded in _sources")
    since = args.since_year or (datetime.date.today().year - 1)
    known = set(store.keys())
    print(f"[topic-watch] {len(queries)} recorded queries; scanning for papers >= {since} "
          f"not in the {len(store)}-paper store ...")

    found = {}  # paperId -> (paper, query)
    for q, _mode in sorted(queries.items()):
        url = (f"{L.GRAPH}/paper/search?query={urllib.parse.quote(q)}"
               f"&fields={L.FIELDS}&limit={args.limit}&year={since}-")
        data = L.http_get(url)
        rows = (data.get("data") or []) if isinstance(data, dict) else []
        n_new = 0
        for p in rows:
            pid = p.get("paperId")
            if pid and pid not in known and pid not in found:
                found[pid] = (p, q)
                n_new += 1
        print(f"  [{q[:52]:52s}] {len(rows):3d} scanned, {n_new} new")
        time.sleep(L.SLEEP)

    today = datetime.date.today().isoformat()
    dest = os.path.join(args.out, f"watch_{today}.md")
    new_sorted = sorted(found.values(), key=lambda pv: (pv[0].get("year") or 0,
                                                        L._score(pv[0])), reverse=True)
    lines = [f"# Topic watch -- {os.path.basename(args.out)} ({today})", "",
             f"*Re-ran {len(queries)} recorded queries (papers >= {since}). "
             f"{len(found)} new paper(s) not already in the {len(store)}-paper collection. "
             f"Every entry is a live Semantic Scholar record (real by construction).*", ""]
    if not found:
        lines.append("_No new papers. (Expected soon after the collection was built -- this "
                     "is the correct baseline for future diffs.)_")
    for p, q in new_sorted:
        t = p.get("tldr") or {}
        gist = t.get("text") or (p.get("abstract") or "")[:200]
        doi = L._doi(p) or "-"
        lines.append(f"- **{p.get('title')}** ({p.get('year')}, {L._first_author(p)} et al., "
                     f"cites {p.get('citationCount') or 0}, DOI {doi})")
        lines.append(f"  - via query: `{q[:70]}`")
        if gist:
            lines.append(f"  - {gist}")
    with open(dest, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[topic-watch] wrote {dest} ({len(found)} new)")

    if args.merge and found:
        for q in {q for _p, q in found.values()}:
            batch = [p for p, qq in found.values() if qq == q]
            L.merge(store, batch, f"watch:{q}")
        L.save_store(args.out, store)
        print(f"[topic-watch] merged {len(found)} new papers into papers.json "
              f"(store now {len(store)}). Run `lit_search.py report` to refresh papers.md.")


if __name__ == "__main__":
    main()

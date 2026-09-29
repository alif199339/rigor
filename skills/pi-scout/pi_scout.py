"""
pi_scout.py -- prospective-PI discovery from YOUR papers via Semantic Scholar.

Pipeline: register your own papers as seeds -> harvest the recent papers that cite
them / are recommended from them / match your topic queries -> pull the author pool's
metrics in batch -> score authors for PI-likelihood with a transparent additive score
-> emit a shortlist for the agent to web-verify (current position, university tier)
and audit. Every person this tool reports comes from the live Semantic Scholar API
this session -- no model-memory people, no invented metrics.

What the script does NOT claim to know (the agent's web-verification stage owns it):
current job title, university ranking, funding status. S2 `affiliations` are sparse
and often stale -- they are printed as leads, never as facts.

Reuses the lit-review client (http_get, http_post, merge, rate limiting) -- requires
the sibling `.claude/skills/lit-review/lit_search.py`. Stdlib only, Python 3.10+.

Commands
--------
  seed      (--id DOI:10..../ARXIV:...|--title "...") [--out DIR]   # register YOUR paper
  harvest   [--year-from N] [--limit-cites 100] [--limit-recs 30]
            [--query "..." ...] [--limit-search 25] [--out DIR]
  authors   [--keep-coauthors] [--refresh] [--out DIR]              # batch metrics fetch
  shortlist [--top 40] [--min-h 0] [--keep-coauthors] [--out DIR]   # score -> candidates.md
  dossier   --author <authorId> [--limit 100] [--out DIR]           # deep per-PI file
  status    [--out DIR]                                             # store stats + next step

Rate limits: S2_API_KEY is 1 request/second cumulative; the client sleeps 1.2s
between calls and retries 429s with backoff. Windows: set PYTHONUTF8=1.
"""
import argparse
import collections
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# import the lit-review client from the sibling skill folder
_LIT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lit-review")
sys.path.insert(0, _LIT)
try:
    import lit_search as L
except ImportError:
    raise SystemExit("[pi-scout] cannot import lit_search.py -- the lit-review skill "
                     "must sit alongside this one at .claude/skills/lit-review/")

GRAPH = "https://api.semanticscholar.org/graph/v1"
RECS = "https://api.semanticscholar.org/recommendations/v1"
PFIELDS = ("title,abstract,year,authors,venue,externalIds,url,"
           "citationCount,influentialCitationCount,publicationTypes")
PFIELDS_CITING = ",".join("citingPaper." + f for f in PFIELDS.split(","))
# NB: `aliases` was removed from the S2 author schema (400s if requested)
AFIELDS = "name,url,affiliations,homepage,paperCount,citationCount,hIndex,externalIds"
APAPER_FIELDS = "title,year,venue,citationCount,authors,externalIds,publicationTypes"


def _today() -> str:
    return datetime.date.today().isoformat()


def _load(out_dir: str, name: str, default):
    p = os.path.join(out_dir, name)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return default


def _save(out_dir: str, name: str, obj):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def _seed_author_ids(seeds: dict) -> set:
    """You + your coauthors -- excluded from the candidate pool by default
    (you already know them; the point is NEW doors). Anchor seeds (papers central
    to your niche but NOT yours, seeded with --anchor) are skipped: their authors
    are prime candidates, not coauthors."""
    ids = set()
    for s in seeds.values():
        if s.get("_anchor"):
            continue
        for a in (s.get("authors") or []):
            if a.get("authorId"):
                ids.add(a["authorId"])
    return ids


def _pretty_source(src: str, seeds: dict) -> str:
    """cites:<paperId> -> cites-you(<title...>) / cites-anchor(...) for --anchor seeds;
    recs:<paperId> -> related-to(<title...>). search:<query> stays as-is."""
    for prefix in ("cites:", "recs:"):
        if src.startswith(prefix):
            pid = src[len(prefix):]
            s = seeds.get(pid, {})
            t = (s.get("title") or pid[:12])[:45]
            if prefix == "recs:":
                label = "related-to"
            else:
                label = "cites-anchor" if s.get("_anchor") else "cites-you"
            return f"{label}({t})"
    return src


# ---------------- commands ----------------

def cmd_seed(args):
    seeds = _load(args.out, "seeds.json", {})
    fields = "title,year,authors,venue,externalIds,url,citationCount,abstract"
    try:
        if args.id:
            p = L.http_get(f"{GRAPH}/paper/{urllib.parse.quote(args.id)}?fields={fields}")
        else:
            q = urllib.parse.quote(args.title)
            data = L.http_get(f"{GRAPH}/paper/search/match?query={q}&fields={fields}")
            matches = data.get("data") or []
            if not matches:
                print(f"[seed] NO MATCH for title: {args.title}")
                return
            p = matches[0]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"[seed] NO MATCH: {args.id or args.title}")
            return
        raise
    pid = p.get("paperId")
    if not pid:
        print("[seed] API returned a record without a paperId -- not stored")
        return
    known = pid in seeds
    if args.anchor:
        p["_anchor"] = True
    seeds[pid] = p
    _save(args.out, "seeds.json", seeds)
    doi = (p.get("externalIds") or {}).get("DOI", "-")
    kind = "ANCHOR" if args.anchor else "own paper"
    print(f"[seed] {'known' if known else 'NEW'} {kind}: {p.get('title')} ({p.get('year')}) "
          f"DOI={doi} cites={p.get('citationCount')} authors={len(p.get('authors') or [])}")
    if args.anchor:
        print("[seed] anchor = topical seed that is NOT yours; its authors stay candidates "
              "and its citers are labeled cites-anchor, not cites-you")
    else:
        print(f"[seed] {len(seeds)} seed paper(s) on record. If this is NOT your paper, "
              f"re-run with the exact DOI.")
    time.sleep(L.SLEEP)


def _filter_year(papers: list, year_from) -> list:
    """Recent-only pool: papers without a year are dropped too (can't verify recency)."""
    if not year_from:
        return [p for p in papers if p]
    return [p for p in papers if p and (p.get("year") or 0) >= year_from]


def cmd_harvest(args):
    seeds = _load(args.out, "seeds.json", {})
    if not seeds and not args.query:
        raise SystemExit("[harvest] no seeds registered and no --query given -- "
                         "run `seed` first (your own papers are the anchor)")
    store = _load(args.out, "papers.json", {})
    year_from = args.year_from or (datetime.date.today().year - 2)
    print(f"[harvest] window: {year_from}- (papers without a year are skipped)")

    for pid, s in seeds.items():
        # 1) who cites you -- the warmest possible lead
        collected, offset = [], 0
        while len(collected) < args.limit_cites:
            page = min(1000, args.limit_cites - len(collected))
            url = (f"{GRAPH}/paper/{pid}/citations?fields={PFIELDS_CITING}"
                   f"&limit={page}&offset={offset}")
            data = L.http_get(url)
            rows = data.get("data") or []
            collected.extend(r.get("citingPaper") for r in rows)
            time.sleep(L.SLEEP)
            if data.get("next") is None or not rows:
                break
            offset = data["next"]
        recent = _filter_year(collected, year_from)
        n = L.merge(store, recent, f"cites:{pid}")
        print(f"[harvest] citations of '{(s.get('title') or '?')[:50]}': "
              f"{len(collected)} fetched, {len(recent)} in window, {n} new")

        # 2) recommendations seeded on your paper (recent pool)
        url = (f"{RECS}/papers/forpaper/{pid}?fields={PFIELDS}"
               f"&limit={args.limit_recs}&from=recent")
        try:
            data = L.http_get(url)
            recs = _filter_year(data.get("recommendedPapers") or [], year_from)
            n = L.merge(store, recs, f"recs:{pid}")
            print(f"[harvest] recommendations: {len(recs)} in window, {n} new")
        except urllib.error.HTTPError as e:
            print(f"[harvest] recommendations unavailable for {pid} (HTTP {e.code})")
        time.sleep(L.SLEEP)

    # 3) optional topical searches (same relevance-ranked endpoint as lit-review)
    for q in (args.query or []):
        url = (f"{GRAPH}/paper/search?query={urllib.parse.quote(q)}"
               f"&fields={PFIELDS}&limit={args.limit_search}&year={year_from}-")
        data = L.http_get(url)
        papers = data.get("data") or []
        n = L.merge(store, papers, f"search:{q}")
        print(f"[harvest] search '{q}': {len(papers)} returned, {n} new")
        time.sleep(L.SLEEP)

    _save(args.out, "papers.json", store)
    pool = {a["authorId"] for p in store.values()
            for a in (p.get("authors") or []) if a.get("authorId")}
    print(f"[harvest] store: {len(store)} papers, {len(pool)} distinct authors. "
          f"Next: `authors` to fetch their metrics.")


def cmd_authors(args):
    store = _load(args.out, "papers.json", {})
    if not store:
        raise SystemExit("[authors] papers.json is empty -- run `harvest` first")
    seeds = _load(args.out, "seeds.json", {})
    authors = _load(args.out, "authors.json", {})
    exclude = set() if args.keep_coauthors else _seed_author_ids(seeds)

    pool = {}
    for p in store.values():
        for a in (p.get("authors") or []):
            aid = a.get("authorId")
            if aid and aid not in exclude:
                pool.setdefault(aid, a.get("name"))
    todo = [aid for aid in pool
            if args.refresh or authors.get(aid, {}).get("_fetched") is None]
    print(f"[authors] pool {len(pool)} (excluded {len(exclude)} seed coauthors); "
          f"fetching {len(todo)}")

    for i in range(0, len(todo), 500):
        chunk = todo[i:i + 500]
        rows = L.http_post(f"{GRAPH}/author/batch?fields={AFIELDS}", {"ids": chunk})
        got = 0
        for aid, rec in zip(chunk, rows):
            if rec is None:
                authors[aid] = {"name": pool[aid], "_fetched": _today(), "_missing": True}
                continue
            rec["_fetched"] = _today()
            authors[aid] = rec
            got += 1
        print(f"[authors] batch {i // 500 + 1}: {got}/{len(chunk)} resolved")
        _save(args.out, "authors.json", authors)
        time.sleep(L.SLEEP)
    _save(args.out, "authors.json", authors)
    with_h = sum(1 for a in authors.values() if a.get("hIndex") is not None)
    print(f"[authors] {len(authors)} author records ({with_h} with an h-index). "
          f"Next: `shortlist`.")


def _h_points(h):
    if h is None:
        return 0
    for cut, pts in ((40, 10), (25, 8), (15, 6), (8, 4)):
        if h >= cut:
            return pts
    return 0


def _evidence(store: dict, exclude: set) -> dict:
    """authorId -> list of {paperId, year, pos, n, last, first, sources} from the harvest."""
    ev = collections.defaultdict(list)
    for pid, p in store.items():
        auths = p.get("authors") or []
        for i, a in enumerate(auths):
            aid = a.get("authorId")
            if not aid or aid in exclude:
                continue
            ev[aid].append({
                "paperId": pid, "title": p.get("title"), "year": p.get("year"),
                "pos": i + 1, "n": len(auths),
                "last": (i == len(auths) - 1 and len(auths) >= 2),
                "first": i == 0,
                "sources": p.get("_sources") or [],
            })
    return ev


def _score(evidence: list, metrics: dict):
    """Transparent additive score; returns (total, components dict, flags list)."""
    cur = datetime.date.today().year
    n_warm = len({e["paperId"] for e in evidence
                  if any(s.startswith("cites:") for s in e["sources"])})
    n_last = sum(1 for e in evidence if e["last"])
    newest = max((e["year"] or 0) for e in evidence) if evidence else 0
    h = metrics.get("hIndex")
    comp = {
        "warm": 12 * min(n_warm, 3),      # papers of theirs that cite YOUR work
        "senior": 6 * min(n_last, 3),     # last-author roles in the harvest (PI signal)
        "breadth": 3 * min(len(evidence), 5),
        "eminence": _h_points(h),
        "recency": 5 if newest >= cur - 1 else 0,
    }
    flags = []
    if metrics.get("_missing") or h is None:
        flags.append("no-metrics")
    if h is not None and h >= 55:
        flags.append("superstar")     # real, but hardest doors; calibrate odds down
    if (h is not None and h < 8 and n_last == 0
            and (metrics.get("paperCount") or 0) < 15):
        flags.append("likely-student")
    return sum(comp.values()), comp, flags, {"n_warm": n_warm, "n_last": n_last,
                                             "newest": newest}


def cmd_shortlist(args):
    store = _load(args.out, "papers.json", {})
    authors = _load(args.out, "authors.json", {})
    seeds = _load(args.out, "seeds.json", {})
    if not store or not authors:
        raise SystemExit("[shortlist] need papers.json + authors.json -- "
                         "run `harvest` then `authors` first")
    exclude = set() if args.keep_coauthors else _seed_author_ids(seeds)
    ev_map = _evidence(store, exclude)

    names = {}
    for p in store.values():
        for a in (p.get("authors") or []):
            if a.get("authorId") and a.get("name"):
                names.setdefault(a["authorId"], a["name"])

    ranked = []
    for aid, evidence in ev_map.items():
        m = authors.get(aid, {})
        if args.min_h and (m.get("hIndex") or 0) < args.min_h:
            continue
        total, comp, flags, stats = _score(evidence, m)
        ranked.append({
            "authorId": aid, "name": m.get("name") or names.get(aid, "?"),
            "hIndex": m.get("hIndex"), "citationCount": m.get("citationCount"),
            "paperCount": m.get("paperCount"),
            "affiliations_s2": m.get("affiliations") or [],
            "homepage": m.get("homepage"), "url": m.get("url"),
            "score": total, "components": comp, "flags": flags, **stats,
            "evidence": evidence,
        })
    ranked.sort(key=lambda r: (-r["score"], -(r["hIndex"] or 0)))
    top = ranked[:args.top]
    _save(args.out, "candidates.json", top)

    lines = [
        "# PI-scout candidate shortlist (pre-verification)", "",
        f"*Generated {_today()} from {len(store)} harvested papers / "
        f"{len(ev_map)} candidate authors; showing top {len(top)}. "
        f"Metrics are Semantic Scholar values as of the `authors` fetch date.*", "",
        "**This list is NOT the deliverable.** Every candidate still needs the "
        "web-verification stage (current position, university + tier, funding "
        "signals) before they may appear in the final report -- S2 affiliations "
        "are sparse/stale leads, not facts.", "",
        "Score = warm(12/paper citing you or a seed anchor, cap 3) "
        "+ senior(6/last-author role, cap 3) "
        "+ breadth(3/harvested paper, cap 5) + eminence(h>=40:10, 25:8, 15:6, 8:4) "
        "+ recency(5 if active this/last year).", "",
        "| # | Name | h | Cites | Pubs | S2 affiliation (lead only) | Score (w/s/b/e/r) | Flags |",
        "|---|------|---|-------|------|----------------------------|-------------------|-------|",
    ]
    for i, r in enumerate(top, 1):
        aff = "; ".join(r["affiliations_s2"])[:45] or "-"
        c = r["components"]
        who = f"[{r['name']}]({r['url']})" if r.get("url") else r["name"]
        lines.append(
            f"| {i} | {who} | {r['hIndex'] if r['hIndex'] is not None else '?'} "
            f"| {r['citationCount'] if r['citationCount'] is not None else '?'} "
            f"| {r['paperCount'] if r['paperCount'] is not None else '?'} | {aff} "
            f"| **{r['score']}** ({c['warm']}/{c['senior']}/{c['breadth']}/"
            f"{c['eminence']}/{c['recency']}) | {', '.join(r['flags']) or '-'} |")
    lines += ["", "## Evidence per candidate", ""]
    for i, r in enumerate(top, 1):
        lines.append(f"### {i}. {r['name']} (S2 authorId `{r['authorId']}`)")
        meta = [f"h={r['hIndex']}", f"cites={r['citationCount']}",
                f"pubs={r['paperCount']}"]
        if r["homepage"]:
            meta.append(f"homepage: {r['homepage']}")
        lines.append("- " + " · ".join(meta))
        if r["affiliations_s2"]:
            lines.append(f"- S2 affiliation (verify!): {'; '.join(r['affiliations_s2'])}")
        for e in sorted(r["evidence"], key=lambda e: -(e["year"] or 0)):
            role = "last/senior author" if e["last"] else \
                   ("first author" if e["first"] else f"author {e['pos']}/{e['n']}")
            via = "; ".join(_pretty_source(s, seeds) for s in e["sources"])
            lines.append(f"  - ({e['year']}) {e['title']} — **{role}** · via {via}")
        lines.append("")
    dest = os.path.join(args.out, "candidates.md")
    with open(dest, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[shortlist] {len(top)} candidates -> {dest}")
    print("[shortlist] next: agent web-verifies each keeper, then `dossier --author <id>`")


def cmd_dossier(args):
    aid = args.author
    seeds = _load(args.out, "seeds.json", {})
    store = _load(args.out, "papers.json", {})
    a = L.http_get(f"{GRAPH}/author/{urllib.parse.quote(aid)}?fields={AFIELDS}")
    time.sleep(L.SLEEP)
    data = L.http_get(f"{GRAPH}/author/{urllib.parse.quote(aid)}/papers"
                      f"?fields={APAPER_FIELDS}&limit={min(args.limit, 1000)}")
    papers = data.get("data") or []
    time.sleep(L.SLEEP)

    cur = datetime.date.today().year
    by_year = collections.Counter(p["year"] for p in papers if p.get("year"))
    multi = [p for p in papers if len(p.get("authors") or []) >= 2]

    def role_stats(pool):
        last = first = 0
        for p in pool:
            auths = p.get("authors") or []
            ids = [x.get("authorId") for x in auths]
            if aid in ids:
                if ids.index(aid) == len(ids) - 1:
                    last += 1
                elif ids.index(aid) == 0:
                    first += 1
        return last, first

    last_all, first_all = role_stats(multi)
    recent3 = [p for p in multi if (p.get("year") or 0) >= cur - 2]
    last_3y, first_3y = role_stats(recent3)

    def pct(n, d):
        return f"{100 * n / d:.0f}%" if d else "n/a"

    recent5 = [p for p in papers if (p.get("year") or 0) >= cur - 4]
    venues = collections.Counter((p.get("venue") or "").strip()
                                 for p in recent5 if (p.get("venue") or "").strip())
    collab = collections.Counter()
    for p in recent5:
        for x in (p.get("authors") or []):
            if x.get("authorId") and x["authorId"] != aid and x.get("name"):
                collab[x["name"]] += 1
    top_cited = sorted(papers, key=lambda p: -(p.get("citationCount") or 0))[:5]
    recent10 = sorted(papers, key=lambda p: (-(p.get("year") or 0),
                                             -(p.get("citationCount") or 0)))[:10]
    evidence = _evidence(store, exclude=set()).get(aid, [])

    years = sorted(y for y in by_year if y >= cur - 9)
    histo = "  ".join(f"{y}:{by_year[y]}" for y in years) or "(no dated papers)"
    orcid = (a.get("externalIds") or {}).get("ORCID", "-")

    lines = [
        f"# Dossier: {a.get('name')} — S2 authorId `{aid}`", "",
        f"*S2 metrics as of {_today()}: h-index **{a.get('hIndex')}**, "
        f"citations **{a.get('citationCount')}**, papers **{a.get('paperCount')}**. "
        f"Fetched {len(papers)} most recent papers for the analysis below.*", "",
        f"- S2 profile: {a.get('url') or '-'}",
        f"- Homepage (S2-listed): {a.get('homepage') or '-'} · ORCID: {orcid}",
        f"- S2 affiliations (**lead only, often stale -- verify**): "
        f"{'; '.join(a.get('affiliations') or []) or '(none listed)'}",
        "",
        "## Publication trajectory",
        f"- papers/year (last 10y): {histo}",
        f"- last-author (senior) share, multi-author papers: overall "
        f"{pct(last_all, len(multi))} · last 3y {pct(last_3y, len(recent3))} "
        f"({last_3y}/{len(recent3)}) <- the PI signal: a rising/high recent share "
        f"means they run the group",
        f"- first-author share last 3y: {pct(first_3y, len(recent3))} "
        f"(high first-author + low last-author = likely still a junior researcher)",
        "",
        "## Top venues (last 5y)",
        *([f"- {v} — {c} paper(s)" for v, c in venues.most_common(8)] or ["- (none)"]),
        "",
        "## Frequent collaborators (last 5y) — likely group members / co-PIs",
        *([f"- {n} — {c} shared paper(s)" for n, c in collab.most_common(8)] or ["- (none)"]),
        "",
        "## Most-cited papers",
        *[f"- ({p.get('year')}) {p.get('title')} — {p.get('citationCount')} cites"
          for p in top_cited],
        "",
        "## Most recent papers",
        *[f"- ({p.get('year')}) {p.get('title')} · {p.get('venue') or '-'}"
          for p in recent10],
        "",
        "## Connection to YOUR work (from the harvest)",
    ]
    if evidence:
        for e in sorted(evidence, key=lambda e: -(e["year"] or 0)):
            role = "last/senior author" if e["last"] else \
                   ("first author" if e["first"] else f"author {e['pos']}/{e['n']}")
            via = "; ".join(_pretty_source(s, seeds) for s in e["sources"])
            lines.append(f"- ({e['year']}) {e['title']} — **{role}** · via {via}")
    else:
        lines.append("- (no harvested paper links this author to your seeds -- "
                     "connection is topical only)")
    lines += [
        "",
        "## Web verification — AGENT FILLS; every fact needs a source URL + accessed date",
        "- [ ] Current position/title (Asst/Assoc/Full Prof or equivalent):",
        "- [ ] Institution + department:",
        "- [ ] University rank (QS and/or THE, state which + year) + country -> tier:",
        "- [ ] Lab/group page + approximate group size:",
        "- [ ] Funding signals (named grants, 'openings' page, recent RA/PhD ads):",
        "- [ ] Contact email (official page only):",
        "",
        "## Audit & verdict — AGENT FILLS (grounded in the sections above + profile.yaml)",
        "- Fit with my profile (topic + methods overlap, cite the evidence):",
        "- What they would value in me (map my skills to their gaps):",
        "- Funding likelihood for an RA-ship (evidence-based):",
        "- Entry probability: High / Medium / Low — with the reasoning:",
        "- Outreach angle (the one concrete hook to open the email with):",
    ]
    dossier_dir = os.path.join(args.out, "dossiers")
    os.makedirs(dossier_dir, exist_ok=True)
    slug = re.sub(r"[^\w]+", "_", a.get("name") or aid).strip("_")[:40]
    dest = os.path.join(dossier_dir, f"{slug}_{aid}.md")
    with open(dest, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[dossier] {a.get('name')}: {len(papers)} papers analyzed -> {dest}")


def cmd_status(args):
    seeds = _load(args.out, "seeds.json", {})
    store = _load(args.out, "papers.json", {})
    authors = _load(args.out, "authors.json", {})
    cands = _load(args.out, "candidates.json", [])
    dossiers = []
    ddir = os.path.join(args.out, "dossiers")
    if os.path.isdir(ddir):
        dossiers = [f for f in os.listdir(ddir) if f.endswith(".md")]
    print(f"[status] out={args.out}")
    print(f"  seeds:      {len(seeds)}")
    print(f"  papers:     {len(store)}")
    print(f"  authors:    {len(authors)} fetched")
    print(f"  candidates: {len(cands)} shortlisted")
    print(f"  dossiers:   {len(dossiers)}")
    for step, done in (("seed", seeds), ("harvest", store), ("authors", authors),
                       ("shortlist", cands), ("dossier", dossiers)):
        if not done:
            print(f"  next step -> {step}")
            break


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="pi-scout", help="working folder (default pi-scout/)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seed", help="register one of YOUR papers (verified via S2)")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--id", help="DOI:10.../ARXIV:2101.00001/S2 paperId")
    g.add_argument("--title", help="exact-ish title (S2 match endpoint)")
    s.add_argument("--anchor", action="store_true",
                   help="paper is a topical anchor, NOT yours (use when your own papers "
                        "are not yet indexed): its authors stay in the candidate pool "
                        "and its citers are labeled cites-anchor")
    s.set_defaults(func=cmd_seed)

    h = sub.add_parser("harvest", help="citing + recommended + searched recent papers")
    h.add_argument("--year-from", type=int, default=None,
                   help="recency window (default: current year - 2)")
    h.add_argument("--limit-cites", type=int, default=100)
    h.add_argument("--limit-recs", type=int, default=30)
    h.add_argument("--query", action="append", default=[],
                   help="optional topical search, repeatable")
    h.add_argument("--limit-search", type=int, default=25)
    h.set_defaults(func=cmd_harvest)

    a = sub.add_parser("authors", help="batch-fetch metrics for the author pool")
    a.add_argument("--keep-coauthors", action="store_true",
                   help="do NOT exclude your seeds' coauthors from the pool")
    a.add_argument("--refresh", action="store_true", help="refetch already-known authors")
    a.set_defaults(func=cmd_authors)

    r = sub.add_parser("shortlist", help="score the pool -> candidates.md/json")
    r.add_argument("--top", type=int, default=40)
    r.add_argument("--min-h", type=int, default=0,
                   help="drop authors below this h-index (0 = keep all, rely on score)")
    r.add_argument("--keep-coauthors", action="store_true")
    r.set_defaults(func=cmd_shortlist)

    d = sub.add_parser("dossier", help="deep per-author analysis -> dossiers/<name>.md")
    d.add_argument("--author", required=True, help="S2 authorId (from candidates.md)")
    d.add_argument("--limit", type=int, default=100, help="papers to analyze (max 1000)")
    d.set_defaults(func=cmd_dossier)

    st = sub.add_parser("status", help="store stats + suggested next step")
    st.set_defaults(func=cmd_status)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

"""
lit_search.py -- Semantic Scholar API client for grounded literature reviews.

Every paper this tool reports comes from the live Semantic Scholar API (200M+ papers),
so titles / DOIs / URLs are real by construction -- the whole point is to bypass
LLM-hallucinated citations. Stdlib only (urllib), Python 3.10+.

State model: each command merges its finds into <out>/papers.json (deduped by S2
paperId, tagged with which query/seed found them). `report` regenerates papers.md
(human-readable, ranked) and papers.bib (BibTeX, taken from S2's own citationStyles
field when available) from that JSON. `pdfs` downloads open-access PDFs.

Commands
--------
  search    --query "..." [--limit 25] [--year-from 2015] [--bulk --max 300] [--out DIR]
  lookup    (--id DOI:10.../ARXIV:1234.5678/S2-hex | --title "exact-ish title") [--out DIR]
  snowball  --seed <paperId-or-DOI:...> [--direction refs|cites|both] [--limit 50] [--out DIR]
  contexts  --seed <paperId-or-DOI:...> [--limit 100]   # citing sentences + intent tags
  recommend --seed <paperId-or-DOI:...> [--limit 20] [--out DIR]
  enrich    --source openalex [--out DIR]    # fill missing abstracts from OpenAlex (own field)
  refresh   [--out DIR]                      # batch-refresh citation counts + stamp fetch date
  report    [--focus "..."|--focus-file F]   # rich per-paper entries (title/links/abstract);
                                             # with a focus: relevance-ranked + tiered
  pdfs      [--top 10] [--out DIR]           # download open-access PDFs (arXiv fallback)
  fulltext  [--top 10 | --all] [--force]     # pypdf-extract page-tagged text from pdfs/

Rate limits: the public (keyless) pool is heavily throttled -- this client sleeps
between calls and retries on 429/5xx with backoff. For reliability, get a free key
at https://www.semanticscholar.org/product/api and set S2_API_KEY; it is sent as
the x-api-key header automatically.
"""
import argparse
import datetime
import difflib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Windows consoles default to cp1252; abstracts are full of unicode.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

GRAPH = "https://api.semanticscholar.org/graph/v1"
RECS = "https://api.semanticscholar.org/recommendations/v1"
OPENALEX = "https://api.openalex.org/works"
FIELDS = ("title,abstract,year,authors,venue,journal,externalIds,url,"
          "openAccessPdf,citationCount,influentialCitationCount,"
          "publicationTypes,tldr,citationStyles")
# The /references, /citations, and recommendations endpoints reject tldr and
# citationStyles (HTTP 400) -- they only take core paper fields.
FIELDS_LINKED = ("title,abstract,year,authors,venue,journal,externalIds,url,"
                 "openAccessPdf,citationCount,influentialCitationCount,"
                 "publicationTypes")
# /paper/search/bulk rejects tldr/citationStyles too; same core-field set as linked.
FIELDS_BULK = FIELDS_LINKED
# citation-context fields (the /citations endpoint carries contexts+intents)
FIELDS_CTX = ("contexts,intents,citingPaper.title,citingPaper.year,"
              "citingPaper.authors,citingPaper.externalIds,citingPaper.venue")
SLEEP = 1.2          # polite gap between calls (keyless pool)
RETRIES = 8          # keyless pool 429s often; patience wins


def http_get(url: str) -> dict:
    headers = {"User-Agent": "lit-review-skill/1.0 (research literature survey)"}
    key = os.environ.get("S2_API_KEY")
    if key:
        headers["x-api-key"] = key
    last_err = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 500, 502, 503, 504):
                # keyless pool is shared globally; long, growing waits are normal
                wait = min(5 * (attempt + 1) + 2 ** attempt, 90)
                print(f"  [retry] HTTP {e.code}, waiting {wait}s ...", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
        except urllib.error.URLError as e:
            last_err = e
            time.sleep(min(2 ** (attempt + 1), 30))
    raise SystemExit(f"[fatal] API unreachable after {RETRIES} retries: {last_err}")


def http_post(url: str, payload: dict) -> list:
    headers = {"User-Agent": "lit-review-skill/1.0", "Content-Type": "application/json"}
    key = os.environ.get("S2_API_KEY")
    if key:
        headers["x-api-key"] = key
    body = json.dumps(payload).encode("utf-8")
    last_err = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 500, 502, 503, 504):
                wait = min(5 * (attempt + 1) + 2 ** attempt, 90)
                print(f"  [retry] HTTP {e.code}, waiting {wait}s ...", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
        except Exception as e:
            last_err = e
            time.sleep(min(2 ** (attempt + 1), 30))
    raise SystemExit(f"[fatal] batch POST failed after {RETRIES} retries: {last_err}")


def openalex_get(url: str):
    """OpenAlex is free + keyless; the polite pool just wants a mailto. Never send the
    S2 key here."""
    mailto = os.environ.get("OPENALEX_MAILTO") or os.environ.get("CROSSREF_MAILTO") or "research@example.com"
    sep = "&" if "?" in url else "?"
    full = url + f"{sep}mailto={urllib.parse.quote(mailto)}"
    headers = {"User-Agent": f"lit-review-skill/1.0 (mailto:{mailto})"}
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(full, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                # 404 = no such work; 400 = OpenAlex rejected the query itself.
                # Neither is retryable, and one bad record must not abort a
                # checkpointed batch run (enrich loops over many titles).
                return None
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(min(3 * (attempt + 1), 30))
                continue
            raise
        except Exception:
            # URLError, RemoteDisconnected, timeouts, resets -- all retryable here
            time.sleep(min(2 ** (attempt + 1), 20))
    return None


def _oa_filter_value(text: str) -> str:
    """OpenAlex filter syntax reserves ',' (filter separator) and '|' (OR) with no
    escape mechanism -- URL-encoding does not help, the API decodes before parsing --
    so a title containing either draws an HTTP 400. Replace them with spaces."""
    return re.sub(r"\s+", " ", (text or "").replace(",", " ").replace("|", " ")).strip()


def reconstruct_abstract(inv: dict):
    """OpenAlex returns an abstract_inverted_index {word: [positions]}; rebuild plain text."""
    if not inv:
        return None
    positions = [(i, w) for w, idxs in inv.items() for i in idxs]
    positions.sort()
    return " ".join(w for _, w in positions).strip() or None


# ---------------- store ----------------

def store_path(out_dir: str) -> str:
    return os.path.join(out_dir, "papers.json")


def load_store(out_dir: str) -> dict:
    p = store_path(out_dir)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_store(out_dir: str, store: dict):
    os.makedirs(out_dir, exist_ok=True)
    with open(store_path(out_dir), "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=1)


def merge(store: dict, papers: list, source: str) -> int:
    """Merge API paper dicts into the store; tag provenance. Returns # new."""
    new = 0
    for p in papers:
        if not p or not p.get("paperId"):
            continue
        pid = p["paperId"]
        if pid in store:
            srcs = store[pid].setdefault("_sources", [])
            if source not in srcs:
                srcs.append(source)
            # fill any fields the earlier fetch was missing
            for k, v in p.items():
                if store[pid].get(k) in (None, [], "") and v not in (None, [], ""):
                    store[pid][k] = v
        else:
            p["_sources"] = [source]
            store[pid] = p
            new += 1
    return new


# ---------------- commands ----------------

def cmd_search(args):
    store = load_store(args.out)
    q = urllib.parse.quote(args.query)
    if args.bulk:
        return _search_bulk(args, store, q)
    url = f"{GRAPH}/paper/search?query={q}&fields={FIELDS}&limit={args.limit}"
    if args.year_from:
        url += f"&year={args.year_from}-"
    data = http_get(url)
    papers = data.get("data") or []
    n = merge(store, papers, f"search:{args.query}")
    save_store(args.out, store)
    print(f"[search] '{args.query}': {len(papers)} returned, {n} new "
          f"(store now {len(store)} papers)")
    time.sleep(SLEEP)


def _search_bulk(args, store, q):
    """Exhaustive coverage pass: /paper/search/bulk returns up to 1,000/page,
    token-paginated, NO relevance ranking and no tldr. For long-tail / "no paper does X"
    claims. Capped at --max so a broad query can't balloon the store."""
    base = f"{GRAPH}/paper/search/bulk?query={q}&fields={FIELDS_BULK}"
    if args.year_from:
        base += f"&year={args.year_from}-"
    collected, token, total = [], None, None
    while len(collected) < args.max:
        url = base + (f"&token={token}" if token else "")
        data = http_get(url)
        if total is None:
            total = data.get("total")
        batch = data.get("data") or []
        collected.extend(batch)
        token = data.get("token")
        if not token or not batch:
            break
        time.sleep(SLEEP)
    collected = collected[:args.max]
    n = merge(store, collected, f"bulk:{args.query}")
    save_store(args.out, store)
    print(f"[search --bulk] '{args.query}': {total} total match; pulled {len(collected)} "
          f"(--max {args.max}), {n} new (store now {len(store)} papers)")


def cmd_lookup(args):
    store = load_store(args.out)
    if args.id:
        url = f"{GRAPH}/paper/{urllib.parse.quote(args.id)}?fields={FIELDS}"
        p = http_get(url)
        n = merge(store, [p], f"lookup:{args.id}")
    else:
        q = urllib.parse.quote(args.title)
        url = f"{GRAPH}/paper/search/match?query={q}&fields={FIELDS}"
        data = http_get(url)
        matches = data.get("data") or []
        if not matches:
            print(f"[lookup] NO MATCH for title: {args.title}")
            return
        p = matches[0]
        n = merge(store, [p], f"lookup-title:{args.title[:40]}")
    save_store(args.out, store)
    doi = (p.get("externalIds") or {}).get("DOI", "-")
    print(f"[lookup] {'NEW' if n else 'known'}: {p.get('title')} ({p.get('year')}) "
          f"DOI={doi} cites={p.get('citationCount')}")
    time.sleep(SLEEP)


def cmd_snowball(args):
    store = load_store(args.out)
    directions = ["references", "citations"] if args.direction == "both" else \
                 (["references"] if args.direction == "refs" else ["citations"])
    for d in directions:
        wrap = "citedPaper" if d == "references" else "citingPaper"
        url = (f"{GRAPH}/paper/{urllib.parse.quote(args.seed)}/{d}"
               f"?fields={FIELDS_LINKED}&limit={args.limit}")
        data = http_get(url)
        # S2 returns {"data": null} -- a literal null, not a missing key -- for some
        # papers' link lists, so a plain .get(k, []) default would pass None into the loop
        papers = [row.get(wrap) for row in (data.get("data") or [])]
        n = merge(store, papers, f"snowball-{d}:{args.seed}")
        print(f"[snowball] {d} of {args.seed}: {len(papers)} returned, {n} new")
        time.sleep(SLEEP)
    save_store(args.out, store)
    print(f"[snowball] store now {len(store)} papers")


def cmd_contexts(args):
    """Fetch the sentences in which papers cite a seed, tagged with intent
    (methodology/background/result). Upgrades novelty screening from regex-over-abstracts
    to reading how the field actually uses a method."""
    store = load_store(args.out)
    seed_title = None
    for p in store.values():
        ext = p.get("externalIds") or {}
        if p.get("paperId") == args.seed or (args.seed.startswith("DOI:")
                and (ext.get("DOI") or "").lower() == args.seed[4:].lower()):
            seed_title = p.get("title")
            break
    url = (f"{GRAPH}/paper/{urllib.parse.quote(args.seed)}/citations"
           f"?fields={FIELDS_CTX}&limit={min(args.limit, 1000)}")
    data = http_get(url)
    rows = (data.get("data") or []) if isinstance(data, dict) else []
    ctx_dir = os.path.join(args.out, "contexts")
    os.makedirs(ctx_dir, exist_ok=True)
    slug = re.sub(r"[^\w]+", "_", args.seed).strip("_")[:50]
    dest = os.path.join(ctx_dir, f"{slug}.md")

    with_ctx = [r for r in rows if r.get("contexts")]
    by_intent = {}
    for r in rows:
        for it in (r.get("intents") or ["(untagged)"]):
            by_intent[it] = by_intent.get(it, 0) + 1
    # lead with the papers that actually expose a citing sentence -- those are the ones
    # worth reading for novelty screening; the sentence-less ones sink to the bottom.
    rows = sorted(rows, key=lambda r: (0 if r.get("contexts") else 1,
                                       -((r.get("citingPaper") or {}).get("year") or 0)))

    lines = [f"# Citation contexts for `{args.seed}`"
             + (f" — {seed_title}" if seed_title else ""), "",
             f"*{len(rows)} citing papers fetched (limit {args.limit}); "
             f"{len(with_ctx)} carry a citing sentence. Intent tallies: "
             + ", ".join(f"{k} {v}" for k, v in sorted(by_intent.items(), key=lambda x: -x[1]))
             + ". Source: Semantic Scholar `/citations`.*", ""]
    for r in rows:
        cp = r.get("citingPaper") or {}
        a = cp.get("authors") or []
        who = (a[0]["name"] if a else "?")
        intents = ", ".join(r.get("intents") or []) or "—"
        lines.append(f"### {cp.get('title') or '?'} ({cp.get('year') or '?'}, {who} et al.)")
        lines.append(f"- intent: **{intents}**  ·  venue: {cp.get('venue') or '-'}")
        for c in (r.get("contexts") or []):
            lines.append(f"  > {c.strip()}")
        if not r.get("contexts"):
            lines.append("  > (no citing sentence exposed by the API)")
        lines.append("")
    with open(dest, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[contexts] {len(rows)} citing papers, {len(with_ctx)} with sentences -> {dest}")
    time.sleep(SLEEP)


def cmd_recommend(args):
    store = load_store(args.out)
    url = (f"{RECS}/papers/forpaper/{urllib.parse.quote(args.seed)}"
           f"?fields={FIELDS_LINKED}&limit={args.limit}")
    data = http_get(url)
    papers = data.get("recommendedPapers", [])
    n = merge(store, papers, f"recommend:{args.seed}")
    save_store(args.out, store)
    print(f"[recommend] for {args.seed}: {len(papers)} returned, {n} new "
          f"(store now {len(store)} papers)")
    time.sleep(SLEEP)


def _first_author(p):
    a = p.get("authors") or []
    return a[0]["name"] if a else "?"


def _doi(p):
    return (p.get("externalIds") or {}).get("DOI")


def _oa_url(p):
    oa = p.get("openAccessPdf") or {}
    if oa.get("url"):
        return oa["url"]
    arx = (p.get("externalIds") or {}).get("ArXiv")
    return f"https://arxiv.org/pdf/{arx}" if arx else None


def _pdf_candidates(p):
    """All plausible PDF URLs, best first: the OA link, then arXiv even if an
    OA link exists (publisher OA links are sometimes HTML landing pages)."""
    urls = []
    oa = p.get("openAccessPdf") or {}
    if oa.get("url"):
        urls.append(oa["url"])
    arx = (p.get("externalIds") or {}).get("ArXiv")
    if arx:
        arx_url = f"https://arxiv.org/pdf/{arx}"
        if arx_url not in urls:
            urls.append(arx_url)
    return urls


def _pdf_basename(p):
    """Deterministic `<year>_<title>` stem shared by the pdfs and fulltext commands."""
    slug = re.sub(r"[^\w\- ]+", "", p.get("title") or p["paperId"])[:80].strip().replace(" ", "_")
    return f"{p.get('year') or 'nd'}_{slug}"


def _score(p):
    return (p.get("influentialCitationCount") or 0) * 3 + (p.get("citationCount") or 0)


def cmd_refresh(args):
    """Re-fetch mutable fields (citation counts, OA link, tldr) for the whole store in
    one batch POST per 500 ids, and stamp each entry with its fetch date. Provenance
    (_sources) and immutable fields are preserved."""
    store = load_store(args.out)
    if not store:
        raise SystemExit("[refresh] store is empty -- run search/snowball first")
    ids = list(store.keys())
    fields = ("title,year,citationCount,influentialCitationCount,openAccessPdf,tldr,"
              "externalIds")
    today = datetime.date.today().isoformat()
    updated = changed = drops = 0
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        data = http_post(f"{GRAPH}/paper/batch?fields={fields}", {"ids": chunk})
        for pid, rec in zip(chunk, data or []):
            if not rec:
                continue
            old = store[pid].get("citationCount")
            for k in ("citationCount", "influentialCitationCount", "openAccessPdf", "tldr"):
                if rec.get(k) is not None:
                    store[pid][k] = rec[k]
            store[pid]["_fetched"] = today
            updated += 1
            new = rec.get("citationCount")
            if isinstance(old, int) and isinstance(new, int) and new != old:
                changed += 1
                if new < old * 0.8:  # a big drop usually means a merged/split S2 record
                    drops += 1
                    print(f"  [!] citations dropped {old}->{new}: {(rec.get('title') or '?')[:56]}")
        print(f"  [batch] {i + len(chunk)}/{len(ids)} refreshed")
        time.sleep(SLEEP)
    save_store(args.out, store)
    print(f"[refresh] {updated} entries updated, {changed} citation counts changed "
          f"({drops} notable drops) -- stamped _fetched={today}")


def _norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def cmd_enrich(args):
    """Fill the '(abstract unavailable)' holes from OpenAlex, into a SEPARATE field so S2
    provenance stays intact. Report/synthesis may use it but must attribute '(via OpenAlex)'."""
    store = load_store(args.out)
    if not store:
        raise SystemExit("[enrich] store is empty -- run search/snowball first")
    targets = [p for p in store.values()
               if not (p.get("abstract") or "").strip() and not p.get("_abstract_openalex")]
    print(f"[enrich] {len(targets)} store entries lack an abstract; querying OpenAlex "
          f"({args.source}) ...")
    filled = 0
    for n, p in enumerate(targets, 1):
        doi = _doi(p)
        work = None
        if doi:
            work = openalex_get(f"{OPENALEX}/doi:{doi}")
        if work is None:  # title fallback, guarded by a similarity check
            title = p.get("title") or ""
            if title:
                d = openalex_get(f"{OPENALEX}?filter=title.search:"
                                 f"{urllib.parse.quote(_oa_filter_value(title))}&per_page=1")
                cand = ((d or {}).get("results") or [None])[0]
                if cand and difflib.SequenceMatcher(
                        None, _norm_title(title), _norm_title(cand.get("title") or "")).ratio() >= 0.85:
                    work = cand
        abs_text = reconstruct_abstract((work or {}).get("abstract_inverted_index"))
        if abs_text:
            p["_abstract_openalex"] = abs_text
            p["_abstract_source"] = "openalex"
            p["_openalex_id"] = (work or {}).get("id")
            filled += 1
            print(f"  [+] {(p.get('title') or '?')[:62]}  ({len(abs_text)} chars)")
        else:
            print(f"  [--] no OpenAlex abstract: {(p.get('title') or '?')[:56]}")
        if n % 10 == 0:
            save_store(args.out, store)  # checkpoint: OpenAlex can drop mid-run
        time.sleep(0.5)  # OpenAlex polite pool
    save_store(args.out, store)
    print(f"[enrich] filled {filled}/{len(targets)} abstract(s) via OpenAlex (store saved)")


# ---------------- relevance scoring (for `report --focus`) ----------------
# A transparent lexical heuristic, not an embedding: IDF-weighted term overlap between
# the focus text and each paper's title (x3) / tldr (x2) / abstract (x1), normalized to
# the collection max and bucketed into Core / Related / Peripheral tiers. Deliberately
# simple and inspectable -- the matched terms are printed per paper so a human can see
# WHY something ranked where it did (and re-rank by eye for final calls).

_STOP = {"the", "a", "an", "and", "or", "of", "for", "in", "on", "to", "with", "by",
         "from", "at", "is", "are", "was", "were", "be", "been", "that", "this",
         "these", "those", "its", "as", "we", "our", "their", "using", "based", "via",
         "can", "into", "over", "under", "between", "toward", "towards", "new", "novel",
         "approach", "method", "paper", "study", "propose", "proposed"}


def _tokens(text):
    return [t for t in re.findall(r"[a-z][a-z0-9\-]{2,}", (text or "").lower())
            if t not in _STOP]


def _relevance(papers, focus):
    """Returns (scores in [0,1], matched-terms lists), aligned with `papers`."""
    import math
    q = set(_tokens(focus))
    if not q:
        raise SystemExit("[report] --focus text contained no usable terms")
    docs, df = [], {}
    for p in papers:
        toks = {"title": set(_tokens(p.get("title"))),
                "tldr": set(_tokens((p.get("tldr") or {}).get("text"))),
                "abstract": set(_tokens(p.get("abstract") or p.get("_abstract_openalex")))}
        docs.append(toks)
        for t in toks["title"] | toks["tldr"] | toks["abstract"]:
            df[t] = df.get(t, 0) + 1
    N = len(papers)
    idf = {t: math.log((N + 1) / (df.get(t, 0) + 1)) + 1.0 for t in q}
    scores, matches = [], []
    for toks in docs:
        s = (sum(3 * idf[t] for t in q & toks["title"])
             + sum(2 * idf[t] for t in q & toks["tldr"])
             + sum(1 * idf[t] for t in q & toks["abstract"]))
        scores.append(s)
        matches.append(sorted(q & (toks["title"] | toks["tldr"] | toks["abstract"]),
                              key=lambda t: -idf[t]))
    mx = max(scores) if scores and max(scores) > 0 else 1.0
    return [s / mx for s in scores], matches


def _abstract_of(p, cap=650):
    """Best available abstract text + a provenance label (None label = native S2)."""
    if (p.get("abstract") or "").strip():
        text, src = p["abstract"].strip(), None
    elif (p.get("_abstract_openalex") or "").strip():
        text, src = p["_abstract_openalex"].strip(), "abstract via OpenAlex"
    elif ((p.get("tldr") or {}).get("text") or "").strip():
        text, src = p["tldr"]["text"].strip(), "TL;DR (Semantic Scholar)"
    else:
        return None, None
    text = re.sub(r"\s+", " ", text)
    if len(text) > cap:
        text = text[:cap].rsplit(" ", 1)[0] + " …"
    return text, src


def _entry_md(p, rank, rel=None, matched=None):
    """One rich markdown block per paper: title, links, metadata, relevance, abstract."""
    title = (p.get("title") or "(untitled)").strip()
    year = p.get("year") or "?"
    s2 = p.get("url") or f"https://www.semanticscholar.org/paper/{p.get('paperId')}"
    doi = _doi(p)
    oa = _oa_url(p)
    venue = (p.get("venue") or "").strip()
    srcs = ", ".join(sorted({s.split(":", 1)[0] for s in p.get("_sources", [])})) or "-"
    lines = [f"### {rank}. {title} ({year})", ""]
    meta = []
    if venue:
        meta.append(f"*{venue}*")
    meta.append(f"cites {p.get('citationCount') or 0}"
                + (f" (influential {p['influentialCitationCount']})"
                   if p.get("influentialCitationCount") else ""))
    meta.append(f"found via {srcs}")
    lines.append(" · ".join(meta) + "  ")
    links = [f"[Semantic Scholar]({s2})"]
    if doi:
        links.append(f"[DOI:{doi}](https://doi.org/{doi})")
    if oa:
        links.append(f"[open-access PDF]({oa})")
    lines.append("**Links:** " + " · ".join(links) + "  ")
    if rel is not None:
        terms = ", ".join(matched[:7]) if matched else "—"
        lines.append(f"**Relevance:** {rel:.2f} — matches: {terms}  ")
    abs_text, abs_src = _abstract_of(p)
    if abs_text:
        tag = f" *({abs_src})*" if abs_src else ""
        lines.append(f"\n> {abs_text}{tag}")
    else:
        lines.append("\n> *(abstract unavailable — verify before citing claims about "
                     "this paper's content)*")
    lines.append("")
    return lines


TIERS = [(0.60, "Core", "strongly matched to the focus — read these first"),
         (0.30, "Related", "clearly overlapping topics — skim for methods and baselines"),
         (0.00, "Peripheral", "weak lexical overlap — background or false neighbours")]


def cmd_report(args):
    store = load_store(args.out)
    if not store:
        raise SystemExit("[report] store is empty -- run search/snowball first")
    papers = sorted(store.values(), key=_score, reverse=True)
    fetched = sorted(p["_fetched"] for p in papers if p.get("_fetched"))
    asof = (f" Citation counts as of {fetched[0]}"
            + (f"–{fetched[-1]}" if fetched[-1] != fetched[0] else "")
            + " (via `refresh`)." if fetched else "")

    focus = args.focus or ""
    if args.focus_file:
        with open(args.focus_file, encoding="utf-8") as f:
            focus = (focus + "\n" + f.read()).strip()

    lines = [
        "# Literature index — generated from the Semantic Scholar API "
        "(every entry is a real, verified paper)",
        "",
        f"*{len(papers)} unique papers. Regenerate with `report`. Raw metadata: "
        f"`papers.json`. BibTeX: `papers.bib`.{asof}*",
        "",
    ]

    if focus:
        rel, matched = _relevance(papers, focus)
        order = sorted(range(len(papers)), key=lambda i: (-rel[i], -_score(papers[i])))
        preview = re.sub(r"\s+", " ", focus)[:220]
        lines += [
            f"**Ranked by relevance to your focus:** “{preview}”  ",
            "*Relevance = transparent IDF-weighted term overlap (title ×3, TL;DR ×2, "
            "abstract ×1), normalized to the best match — a lexical heuristic with the "
            "matched terms shown per paper, not a black box. Within a tier, ties break "
            "by citation influence.*",
            "",
        ]
        rank = 0
        for _lo, name, blurb in TIERS:
            if name == "Core":
                tier_ids = [i for i in order if rel[i] >= 0.60]
            elif name == "Related":
                tier_ids = [i for i in order if 0.30 <= rel[i] < 0.60]
            else:
                tier_ids = [i for i in order if rel[i] < 0.30]
            if not tier_ids:
                continue
            lines += [f"## {name} ({len(tier_ids)}) — {blurb}", ""]
            for i in tier_ids:
                rank += 1
                lines += _entry_md(papers[i], rank, rel=rel[i], matched=matched[i])
    else:
        lines += ["## All papers, ranked by influence "
                  "(3× influential citations + citations)",
                  "*Tip: pass `--focus \"your project description\"` (or "
                  "`--focus-file abstract.txt`) to re-rank and tier this report by "
                  "relevance to your own work.*", ""]
        for rank, p in enumerate(papers, 1):
            lines += _entry_md(p, rank)

    with open(os.path.join(args.out, "papers.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    bibs = []
    for p in papers:
        bib = (p.get("citationStyles") or {}).get("bibtex")
        if not bib:  # minimal fallback entry
            key = re.sub(r"\W+", "", (_first_author(p).split()[-1] if _first_author(p) != "?" else "anon")
                         + str(p.get("year") or ""))
            doi = _doi(p)
            bib = ("@article{" + key + ",\n"
                   f"  title = {{{p.get('title')}}},\n"
                   f"  year = {{{p.get('year')}}},\n"
                   + (f"  doi = {{{doi}}},\n" if doi else "")
                   + "}")
        bibs.append(bib.strip())
    with open(os.path.join(args.out, "papers.bib"), "w", encoding="utf-8") as f:
        f.write("\n\n".join(bibs) + "\n")
    print(f"[report] wrote papers.md + papers.bib ({len(papers)} papers) in {args.out}")


def cmd_pdfs(args):
    store = load_store(args.out)
    papers = sorted(store.values(), key=_score, reverse=True)
    pdf_dir = os.path.join(args.out, "pdfs")
    os.makedirs(pdf_dir, exist_ok=True)
    got = tried = 0
    for p in papers:
        if got >= args.top:
            break
        urls = _pdf_candidates(p)
        if not urls:
            continue
        tried += 1
        dest = os.path.join(pdf_dir, _pdf_basename(p) + ".pdf")
        if os.path.exists(dest):
            got += 1
            continue
        saved = False
        for url in urls:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research; lit-review-skill)"})
                with urllib.request.urlopen(req, timeout=90) as r:
                    data = r.read()
            except Exception as e:
                print(f"  [skip-url] {(p.get('title') or '?')[:60]} -- {e}")
                time.sleep(SLEEP)
                continue
            time.sleep(SLEEP)
            # real PDFs carry the %PDF magic near the start; landing pages don't
            if b"%PDF" not in data[:1024] or len(data) < 10_000:
                print(f"  [skip-url] not a real PDF ({len(data)} bytes): {url[:70]}")
                continue
            with open(dest, "wb") as f:
                f.write(data)
            got += 1
            saved = True
            print(f"  [pdf {got}/{args.top}] {os.path.basename(dest)}")
            break
        if not saved:
            print(f"  [no-pdf] {(p.get('title') or '?')[:60]}")
    print(f"[pdfs] downloaded {got} PDFs into {pdf_dir} (attempted {tried})")


def cmd_fulltext(args):
    """Extract page-tagged plain text from the downloaded PDFs so claims can be grounded
    in paper *bodies* (equations, method sections), not just abstracts."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise SystemExit(
            "[fulltext] pypdf is not installed in this interpreter. Install it "
            "(`python -m pip install pypdf`) or re-run with an interpreter that has it "
            "(the project profile's python_pypdf).")
    pdf_dir = os.path.join(args.out, "pdfs")
    if not os.path.isdir(pdf_dir):
        raise SystemExit(f"[fulltext] no pdfs/ dir in {args.out} -- run `pdfs` first")
    ft_dir = os.path.join(args.out, "fulltext")
    os.makedirs(ft_dir, exist_ok=True)

    store = load_store(args.out)
    ranked = sorted(store.values(), key=_score, reverse=True)
    # process in influence order, restricted to PDFs actually on disk
    targets = []
    for p in ranked:
        pdf = os.path.join(pdf_dir, _pdf_basename(p) + ".pdf")
        if os.path.exists(pdf):
            targets.append((p, pdf))
    # include any stray PDFs not matched to a store entry (renamed/manual drops)
    matched = {t[1] for t in targets}
    for fn in sorted(os.listdir(pdf_dir)):
        full = os.path.join(pdf_dir, fn)
        if fn.lower().endswith(".pdf") and full not in matched:
            targets.append((None, full))
    if not args.all:
        targets = targets[:args.top]

    done = 0
    for p, pdf in targets:
        stem = os.path.splitext(os.path.basename(pdf))[0]
        dest = os.path.join(ft_dir, stem + ".txt")
        if os.path.exists(dest) and not args.force:
            print(f"  [skip] {stem}.txt exists (use --force to re-extract)")
            done += 1
            continue
        try:
            reader = PdfReader(pdf)
        except Exception as e:
            print(f"  [error] {stem}: {e}")
            continue
        out = []
        if p:  # provenance header ties the text back to the verified record
            out.append(f"# {p.get('title')} ({p.get('year')})  DOI={_doi(p) or '-'}  "
                       f"paperId={p.get('paperId')}\n")
        for i, page in enumerate(reader.pages, 1):
            out.append(f"\n===== PDF PAGE {i} =====\n{page.extract_text() or ''}")
        text = "".join(out)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(text)
        done += 1
        print(f"  [fulltext {done}] {stem}.txt  ({len(reader.pages)} pages, {len(text):,} chars)")
    print(f"[fulltext] extracted {done} document(s) into {ft_dir}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="literature/untitled", help="topic folder (holds papers.json/md/bib, pdfs/)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search"); s.add_argument("--query", required=True)
    s.add_argument("--limit", type=int, default=25); s.add_argument("--year-from", type=int)
    s.add_argument("--bulk", action="store_true", help="exhaustive coverage pass (no ranking)")
    s.add_argument("--max", type=int, default=300, help="cap for --bulk (default 300)")
    s.set_defaults(fn=cmd_search)

    l = sub.add_parser("lookup"); g = l.add_mutually_exclusive_group(required=True)
    g.add_argument("--id"); g.add_argument("--title"); l.set_defaults(fn=cmd_lookup)

    sn = sub.add_parser("snowball"); sn.add_argument("--seed", required=True)
    sn.add_argument("--direction", choices=["refs", "cites", "both"], default="both")
    sn.add_argument("--limit", type=int, default=50); sn.set_defaults(fn=cmd_snowball)

    ct = sub.add_parser("contexts"); ct.add_argument("--seed", required=True)
    ct.add_argument("--limit", type=int, default=100); ct.set_defaults(fn=cmd_contexts)

    r = sub.add_parser("recommend"); r.add_argument("--seed", required=True)
    r.add_argument("--limit", type=int, default=20); r.set_defaults(fn=cmd_recommend)

    en = sub.add_parser("enrich"); en.add_argument("--source", default="openalex", choices=["openalex"])
    en.set_defaults(fn=cmd_enrich)

    rf = sub.add_parser("refresh"); rf.set_defaults(fn=cmd_refresh)

    rp = sub.add_parser("report")
    rp.add_argument("--focus", help="your project/inquiry text -- re-ranks and tiers "
                                    "the report by relevance to it")
    rp.add_argument("--focus-file", help="file with the focus text (e.g. your abstract); "
                                         "concatenated with --focus if both given")
    rp.set_defaults(fn=cmd_report)

    pd = sub.add_parser("pdfs"); pd.add_argument("--top", type=int, default=10)
    pd.set_defaults(fn=cmd_pdfs)

    ft = sub.add_parser("fulltext"); ft.add_argument("--top", type=int, default=10)
    ft.add_argument("--all", action="store_true"); ft.add_argument("--force", action="store_true")
    ft.set_defaults(fn=cmd_fulltext)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

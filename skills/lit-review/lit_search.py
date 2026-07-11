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
  report    [--out DIR]                      # regenerate papers.md + papers.bib
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
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(min(3 * (attempt + 1), 30))
                continue
            raise
        except Exception:
            # URLError, RemoteDisconnected, timeouts, resets -- all retryable here
            time.sleep(min(2 ** (attempt + 1), 20))
    return None


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
    papers = data.get("data", [])
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
        matches = data.get("data", [])
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
        papers = [row.get(wrap) for row in data.get("data", [])]
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
    rows = data.get("data", []) if isinstance(data, dict) else []
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
                d = openalex_get(f"{OPENALEX}?filter=title.search:{urllib.parse.quote(title)}&per_page=1")
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


def cmd_report(args):
    store = load_store(args.out)
    if not store:
        raise SystemExit("[report] store is empty -- run search/snowball first")
    papers = sorted(store.values(), key=_score, reverse=True)
    fetched = sorted(p["_fetched"] for p in papers if p.get("_fetched"))
    asof = (f" Citation counts as of {fetched[0]}"
            + (f"–{fetched[-1]}" if fetched[-1] != fetched[0] else "")
            + " (via `refresh`)." if fetched else "")
    lines = [
        "# Literature index (generated from Semantic Scholar API -- every entry is a real, verified paper)",
        "",
        f"*{len(papers)} unique papers. Regenerate with `report`. Raw metadata: `papers.json`. "
        f"BibTeX: `papers.bib`.{asof}*",
        "",
        "## All papers, ranked by influence (3x influential citations + citations)",
        "",
        "| # | Title | Year | Venue | Cites | DOI | OA PDF | Found via |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, p in enumerate(papers, 1):
        title = (p.get("title") or "?").replace("|", "/")
        link = p.get("url") or ""
        doi = _doi(p) or "-"
        oa = "yes" if _oa_url(p) else "-"
        venue = (p.get("venue") or "-").replace("|", "/")[:40]
        srcs = "; ".join(s.split(":", 1)[0] for s in p.get("_sources", []))[:40]
        lines.append(f"| {i} | [{title}]({link}) | {p.get('year') or '?'} | {venue} | "
                     f"{p.get('citationCount') or 0} | {doi} | {oa} | {srcs} |")
    recent = [p for p in papers if (p.get("year") or 0) >= args.recent_since]
    recent.sort(key=lambda p: (p.get("year") or 0, _score(p)), reverse=True)
    lines += ["", f"## Recent work ({args.recent_since}+), newest first", ""]
    for p in recent:
        t = p.get("tldr") or {}
        gist = t.get("text") or (p.get("abstract") or "")[:220]
        if not gist and p.get("_abstract_openalex"):
            gist = p["_abstract_openalex"][:220] + " *(abstract via OpenAlex)*"
        lines.append(f"- **{p.get('title')}** ({p.get('year')}, {_first_author(p)} et al., "
                     f"cites {p.get('citationCount') or 0}) — {gist}")
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

    rp = sub.add_parser("report"); rp.add_argument("--recent-since", type=int, default=2022)
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

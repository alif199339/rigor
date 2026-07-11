"""
bib_audit.py -- verify every entry in a BibTeX file against Semantic Scholar + Crossref.

Grounds a manuscript's bibliography in real, resolvable records so no hallucinated or
silently-drifted citation reaches submission. For each @entry it:
  1. looks the work up by DOI (S2 /paper/DOI:...), falling back to a title match;
  2. cross-checks via Crossref (api.crossref.org) for anything S2 lacks -- books,
     standards, datasets, very new or very old works;
  3. classifies VERIFIED / MISMATCH / NOT-FOUND / NON-PAPER-OK / UNVERIFIABLE and, for
     MISMATCH, emits a field-by-field diff + a suggested corrected BibTeX line.

It NEVER rewrites the .bib -- it only reports. The user approves each fix.

Stdlib only (urllib + difflib), Python 3.10+. Windows: set PYTHONUTF8=1.
Rate limits: S2 key = 1 req/s cumulative (S2_SLEEP >= 1.0); Crossref polite pool via
the --mailto address. Get a free key at https://www.semanticscholar.org/product/api
and set S2_API_KEY (sent as x-api-key automatically); keyless still works but throttles.

Usage:
  python bib_audit.py --bib references.bib
  python bib_audit.py --bib references.bib --out bib_audit_report.md \
                      --mailto you@example.com --only key1,key2
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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

GRAPH = "https://api.semanticscholar.org/graph/v1"
CROSSREF = "https://api.crossref.org/works"
S2_FIELDS = "title,year,venue,externalIds,authors,publicationTypes,journal"
S2_SLEEP = 1.2      # S2 key is 1 req/s cumulative; never drop below 1.0
CR_SLEEP = 0.6      # Crossref polite pool is separate and generous
RETRIES = 6

# publisher / preprint-server names that masquerade as a journal in a lazy @article
_PUBLISHERS = {"otexts", "wiley", "springer", "elsevier", "mit press", "crc press",
               "academic press", "pearson", "mcgraw-hill", "o'reilly", "manning",
               "nber", "arxiv", "preprint"}


# ---------------- HTTP ----------------

def _get(url: str, headers: dict, none_on_404: bool = True):
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:
                return None if none_on_404 else e
            if e.code in (429, 500, 502, 503, 504):
                wait = min(4 * (attempt + 1) + 2 ** attempt, 60)
                print(f"    [retry] HTTP {e.code}, waiting {wait}s ...", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
        except urllib.error.URLError as e:
            last = e
            time.sleep(min(2 ** (attempt + 1), 20))
    print(f"    [warn] unreachable after {RETRIES} tries: {last}", file=sys.stderr)
    return None


def s2_get(path: str):
    headers = {"User-Agent": "bib-audit-skill/1.0"}
    key = os.environ.get("S2_API_KEY")
    if key:
        headers["x-api-key"] = key
    out = _get(GRAPH + path, headers)
    time.sleep(S2_SLEEP)
    return out


def crossref_get(url: str):
    mailto = os.environ.get("CROSSREF_MAILTO", "research@example.com")
    headers = {"User-Agent": f"bib-audit-skill/1.0 (mailto:{mailto})"}
    out = _get(url, headers)
    time.sleep(CR_SLEEP)
    return out


def url_alive(url: str) -> bool:
    headers = {"User-Agent": "Mozilla/5.0 (bib-audit link check)"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as r:
            return 200 <= r.status < 400
    except urllib.error.HTTPError as e:
        # 403/405 from a HEAD-hostile server still means the URL resolves to something
        return e.code in (401, 403, 405, 406, 429)
    except Exception:
        return False


# ---------------- BibTeX parsing (stdlib) ----------------

def parse_bib(text: str) -> list:
    entries = []
    n = len(text)
    for m in re.finditer(r'@(\w+)\s*\{', text):
        etype = m.group(1).lower()
        if etype in ("comment", "preamble", "string"):
            continue
        i, depth = m.end(), 1
        while i < n and depth > 0:
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
            i += 1
        body = text[m.end():i - 1]
        key, _, rest = body.partition(',')
        entries.append({"type": etype, "key": key.strip(), "fields": parse_fields(rest)})
    return entries


def parse_fields(s: str) -> dict:
    fields, i, n = {}, 0, len(s)
    while i < n:
        while i < n and s[i] in " \t\r\n,":
            i += 1
        if i >= n:
            break
        m = re.match(r'(\w+)\s*=\s*', s[i:])
        if not m:
            break
        name = m.group(1).lower()
        i += m.end()
        if i >= n:
            break
        if s[i] == '{':
            depth, i, start = 1, i + 1, i + 1
            while i < n and depth > 0:
                if s[i] == '{':
                    depth += 1
                elif s[i] == '}':
                    depth -= 1
                i += 1
            val = s[start:i - 1]
        elif s[i] == '"':
            i += 1
            start = i
            while i < n and s[i] != '"':
                i += 1
            val = s[start:i]
            i += 1
        else:
            start = i
            while i < n and s[i] not in ",\n":
                i += 1
            val = s[start:i]
        fields[name] = re.sub(r"\s+", " ", val).strip()
    return fields


# ---------------- helpers ----------------

def norm_doi(d):
    if not d:
        return None
    d = d.strip()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d, flags=re.I)
    return d.strip().rstrip(".").lower() or None


def norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def title_sim(a, b):
    return difflib.SequenceMatcher(None, norm_title(a), norm_title(b)).ratio()


def year_diff(a, b):
    """|bib_year - record_year| as int, or None if either isn't a plain 4-digit year."""
    try:
        return abs(int(str(a).strip()[:4]) - int(str(b).strip()[:4]))
    except (TypeError, ValueError):
        return None


def bib_url(fields):
    for k in ("howpublished", "url", "note"):
        v = fields.get(k, "")
        m = re.search(r'\\url\{([^}]+)\}', v) or re.search(r'(https?://\S+)', v)
        if m:
            return m.group(1).rstrip("}")
    return None


def looks_unpublished(fields):
    blob = " ".join(fields.get(k, "") for k in ("journal", "booktitle", "note", "year")).lower()
    return any(w in blob for w in ("submitted", "in press", "under review", "to appear", "preprint"))


def venue_is_publisher(fields):
    v = (fields.get("journal") or fields.get("booktitle") or "").strip().lower()
    return v in _PUBLISHERS


# ---------------- record extraction from API payloads ----------------

def rec_from_s2(p):
    if not p:
        return None
    ext = p.get("externalIds") or {}
    return {"src": "S2", "title": p.get("title"), "year": p.get("year"),
            "venue": p.get("venue") or ((p.get("journal") or {}) or {}).get("name"),
            "doi": norm_doi(ext.get("DOI")), "arxiv": ext.get("ArXiv"),
            "types": p.get("publicationTypes") or []}


def rec_from_crossref(m):
    if not m:
        return None
    yr = None
    for k in ("published-print", "published-online", "issued", "created"):
        dp = (m.get(k) or {}).get("date-parts")
        if dp and dp[0] and dp[0][0]:
            yr = dp[0][0]
            break
    title = (m.get("title") or [None])[0]
    venue = (m.get("container-title") or [None])[0] or (m.get("publisher"))
    return {"src": "Crossref", "title": title, "year": yr,
            "venue": venue, "doi": norm_doi(m.get("DOI")), "arxiv": None,
            "types": [m.get("type", "")], "volume": m.get("volume"), "page": m.get("page")}


def s2_by_doi(doi):
    return rec_from_s2(s2_get(f"/paper/DOI:{urllib.parse.quote(doi)}?fields={S2_FIELDS}"))


def s2_by_title(title):
    d = s2_get(f"/paper/search/match?query={urllib.parse.quote(title)}&fields={S2_FIELDS}")
    rows = (d or {}).get("data") if isinstance(d, dict) else None
    return rec_from_s2(rows[0]) if rows else None


def crossref_by_doi(doi):
    d = crossref_get(f"{CROSSREF}/{urllib.parse.quote(doi)}?mailto="
                     + os.environ.get("CROSSREF_MAILTO", "research@example.com"))
    return rec_from_crossref((d or {}).get("message")) if d else None


def crossref_by_title(title):
    mailto = os.environ.get("CROSSREF_MAILTO", "research@example.com")
    d = crossref_get(f"{CROSSREF}?query.bibliographic={urllib.parse.quote(title)}"
                     f"&rows=5&mailto={mailto}")
    items = (((d or {}).get("message") or {}).get("items")) or []
    best, best_sim = None, 0.0
    for it in items:
        r = rec_from_crossref(it)
        s = title_sim(title, r.get("title") or "")
        if s > best_sim:
            best, best_sim = r, s
    return best


# ---------------- per-entry audit ----------------

def audit_entry(e):
    f = e["fields"]
    title, doi = f.get("title"), norm_doi(f.get("doi"))
    year = f.get("year")
    url = bib_url(f)
    res = {"key": e["key"], "type": e["type"], "title": title, "year": year,
           "venue": f.get("journal") or f.get("booktitle"), "doi": doi, "url": url,
           "verdict": None, "record": None, "diffs": [], "suggest": [], "notes": []}

    # --- path selection ---
    rec = None
    if doi:
        rec = s2_by_doi(doi) or crossref_by_doi(doi)
        if rec is None:
            res["verdict"] = "NOT-FOUND"
            res["notes"].append("DOI resolves in neither S2 nor Crossref -- check the DOI string.")
            return res
    else:
        # a @misc / dataset / standard with a live URL and no DOI is a NON-PAPER
        if e["type"] in ("misc", "online", "electronic") and url and not title_is_papery(f):
            alive = url_alive(url)
            res["verdict"] = "NON-PAPER-OK" if alive else "UNVERIFIABLE"
            res["notes"].append(f"Non-paper resource (dataset/standard/software); URL "
                                f"{'resolves' if alive else 'did NOT resolve'}: {url}")
            return res
        if not title:
            res["verdict"] = "UNVERIFIABLE"
            res["notes"].append("No DOI and no title -- cannot verify.")
            return res
        rec = s2_by_title(title) or crossref_by_title(title)
        if rec is None or title_sim(title, rec.get("title") or "") < 0.55:
            if looks_unpublished(f):
                res["verdict"] = "UNVERIFIABLE"
                res["notes"].append("Appears unpublished/submitted -- not yet indexed. Re-audit after publication.")
            else:
                res["verdict"] = "NOT-FOUND"
                res["notes"].append("No confident title match in S2 or Crossref.")
                if rec:
                    res["notes"].append(f"(closest guess, low similarity: '{rec.get('title')}')")
            return res

    # --- compare against the found record ---
    res["record"] = rec
    conflicting = False
    by_doi = bool(doi)

    sim = title_sim(title, rec.get("title") or "")
    yd = year_diff(year, rec.get("year"))
    # "same work" confidence: an exact DOI hit, or a strong title match with a plausible
    # (<=2 yr) year offset. S2's year is the *earliest* (often preprint) version, so a
    # 1-2 yr gap on a conference paper is expected, NOT an error.
    same_work = by_doi or (sim >= 0.90 and (yd is None or yd <= 2))

    if rec.get("title") and sim < 0.90:
        res["diffs"].append(("title", title, rec["title"], f"similarity {sim:.2f}"))
        if sim < 0.80:
            conflicting = True
            res["suggest"].append(f"title = {{{rec['title']}}}")

    if yd is not None and yd > 0:
        if yd <= 2:
            res["notes"].append(f"year differs by {yd} ({year} vs {rec['year']}) -- typical "
                                f"preprint-vs-publication offset; the .bib value is often the "
                                f"correct published year. Confirm, don't blindly change.")
            if by_doi:  # DOI is authoritative -> the record's year is the real one
                res["suggest"].append(f"year = {{{rec['year']}}}  % if you cite the published version")
        else:
            res["diffs"].append(("year", year, rec["year"], f"differ by {yd}"))
            if by_doi:
                conflicting = True
                res["suggest"].append(f"year = {{{rec['year']}}}")
            else:
                res["notes"].append("large year gap on a title-only match -- this is likely a "
                                    "DIFFERENT edition/record (e.g. a later reprint or a review). "
                                    "Verify the identity before changing anything; no fix auto-suggested.")

    # DOI / eprint enrichment -- only when we're confident it's the same work
    if not doi and same_work:
        if rec.get("doi"):
            res["diffs"].append(("doi", "(none)", rec["doi"], "missing in .bib"))
            res["suggest"].append(f"doi = {{{rec['doi']}}}")
        elif rec.get("arxiv"):
            res["suggest"].append(f"eprint = {{{rec['arxiv']}}}  % arXiv")

    if venue_is_publisher(f):
        res["notes"].append(f"'{res['venue']}' is a publisher, not a journal -- this looks "
                            f"like a book/report; consider @book/@techreport with publisher=.")
    if f.get("note", "").lower().find("verify") >= 0:
        res["notes"].append("Carries a '% VERIFY' note -- confirm volume/pages against the source.")
        if rec.get("volume"):
            res["suggest"].append(f"volume = {{{rec['volume']}}}")
        if rec.get("page"):
            res["suggest"].append(f"pages = {{{rec['page']}}}")

    # a "hard" diff = title drift or a >2yr gap (needs a human); a missing-DOI diff is
    # pure enrichment and keeps the entry VERIFIED.
    hard = any(d[0] != "doi" for d in res["diffs"])
    res["verdict"] = "MISMATCH" if (conflicting or hard) else "VERIFIED"
    return res


def title_is_papery(f):
    # a @misc that is actually a paper (rare) would have journal/booktitle; datasets don't
    return bool(f.get("journal") or f.get("booktitle"))


# ---------------- report ----------------

_ORDER = ["MISMATCH", "NOT-FOUND", "UNVERIFIABLE", "NON-PAPER-OK", "VERIFIED"]


def write_report(results, bib_path, out_path):
    today = datetime.date.today().isoformat()
    counts = {v: sum(1 for r in results if r["verdict"] == v) for v in _ORDER}
    L = [
        f"# BibTeX audit report -- `{os.path.basename(bib_path)}`",
        "",
        f"*Generated {today} by `bib_audit.py` against Semantic Scholar + Crossref. "
        f"{len(results)} entries checked. This report **proposes**; it never edits the "
        f".bib. Apply each fix only after you approve it.*",
        "",
        "## Summary",
        "",
        "| Verdict | Count | Meaning |",
        "|---|---|---|",
        f"| MISMATCH | {counts['MISMATCH']} | found, but a field conflicts -- fix before submission |",
        f"| NOT-FOUND | {counts['NOT-FOUND']} | not in S2 or Crossref -- verify it exists |",
        f"| UNVERIFIABLE | {counts['UNVERIFIABLE']} | unpublished/submitted or uncheckable |",
        f"| NON-PAPER-OK | {counts['NON-PAPER-OK']} | dataset/standard/software, URL resolves |",
        f"| VERIFIED | {counts['VERIFIED']} | matches a real record |",
        "",
        "| Key | Type | Verdict | Headline |",
        "|---|---|---|---|",
    ]
    ranked = sorted(results, key=lambda r: (_ORDER.index(r["verdict"]), r["key"]))
    for r in ranked:
        head = ""
        if r["diffs"]:
            head = "; ".join(f"{d[0]}: `{d[1]}` -> `{d[2]}`" for d in r["diffs"])[:90]
        elif r["notes"]:
            head = r["notes"][0][:90]
        L.append(f"| {r['key']} | {r['type']} | **{r['verdict']}** | {head.replace(chr(124), '/')} |")

    L += ["", "## Details", ""]
    for r in ranked:
        L.append(f"### `{r['key']}` — {r['verdict']}")
        L.append("")
        L.append(f"- **.bib:** {r['title'] or '(no title)'} — {r['year'] or '?'} — "
                 f"{r['venue'] or '?'}" + (f" — DOI {r['doi']}" if r['doi'] else " — (no DOI)"))
        rec = r["record"]
        if rec:
            L.append(f"- **found ({rec['src']}):** {rec.get('title')} — {rec.get('year')} — "
                     f"{rec.get('venue')}" + (f" — DOI {rec['doi']}" if rec.get('doi') else ""))
        for d in r["diffs"]:
            extra = f" ({d[3]})" if len(d) > 3 else ""
            L.append(f"- **diff [{d[0]}]:** `.bib` = `{d[1]}`  vs  found = `{d[2]}`{extra}")
        for nt in r["notes"]:
            L.append(f"- note: {nt}")
        if r["suggest"]:
            L.append("- **suggested BibTeX fields:**")
            L.append("  ```bibtex")
            for s in r["suggest"]:
                L.append(f"  {s}")
            L.append("  ```")
        L.append("")

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print(f"[report] {out_path}  ({', '.join(f'{k}={counts[k]}' for k in _ORDER)})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bib", required=True)
    ap.add_argument("--out", default=None, help="default: <bib-dir>/bib_audit_report.md")
    ap.add_argument("--mailto", default=None, help="Crossref polite-pool contact email")
    ap.add_argument("--only", default=None, help="comma-separated entry keys to audit")
    args = ap.parse_args()

    if args.mailto:
        os.environ["CROSSREF_MAILTO"] = args.mailto
    with open(args.bib, encoding="utf-8") as f:
        entries = parse_bib(f.read())
    if args.only:
        keep = {k.strip() for k in args.only.split(",")}
        entries = [e for e in entries if e["key"] in keep]
    print(f"[bib-audit] {len(entries)} entries from {args.bib}")

    results = []
    for e in entries:
        print(f"  - {e['key']} ...", flush=True)
        results.append(audit_entry(e))

    out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.bib)), "bib_audit_report.md")
    write_report(results, args.bib, out)


if __name__ == "__main__":
    main()

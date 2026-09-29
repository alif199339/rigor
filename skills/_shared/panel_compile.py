"""
panel_compile.py -- merge ref-audit + result-audit outputs (and the agent's
adjudication files) into PANEL_TRIAGE.xlsx for the technical chair.

Reads, per paper, from the audited directory:
  <stem>_ref_audit.json       (ref-audit verdicts, incl. raw reference strings)
  <stem>_result_audit.json    (result-audit candidates)
  <stem>_adjudication.json    (optional; the agent's adjudication pass)

Adjudication schema (written by the agent, one file per paper):
  {
    "refs":   { "[17]": {"decision": "CONFIRM" | "EXCLUDE", "note": "..."} },
    "result": { "C3":   {"decision": "CONFIRMED-INCONSISTENT" | "EXPLAINED" |
                          "EXTRACTION-NOISE", "note": "..."} }
  }

Inclusion rules (factual-evidence framing -- no AI-attribution, no scoring):
  RETRACTED / MISMATCH ref verdicts  -> included (EXCLUDE removes them).
  NOT-FOUND ref verdicts             -> HALLUCINATED-REF when adjudicated CONFIRM,
                                        NOT-FOUND-REF (unadjudicated) otherwise,
                                        dropped when adjudicated EXCLUDE.
  result-audit candidates            -> DATA-INCONSISTENCY, ONLY when adjudicated
                                        CONFIRMED-INCONSISTENT. Unadjudicated
                                        candidates appear as counts on the Summary
                                        sheet, never as findings rows.
  PARSE-FAILED refs                  -> Summary-sheet count only.

Paper ID = PDF filename stem, or the mapping in --id-map (CSV: filename,paper_id
[,title] -- header row optional).

Output: .xlsx via openpyxl when installed; otherwise CSV fallback
(<out>_findings.csv + <out>_summary.csv).

Usage:
  python panel_compile.py --dir papers/ [--out PANEL_TRIAGE.xlsx] [--id-map ids.csv]
"""
import argparse
import csv
import datetime
import glob
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FINDINGS_HEADER = ["Paper ID", "File", "Finding type", "Location",
                   "Exact finding", "Evidence", "Adjudicated"]
SUMMARY_HEADER = ["Paper ID", "File", "Refs total", "Refs verified", "Hallucinated (confirmed)",
                  "Not-found (unadjudicated)", "Retracted", "Ref mismatches", "Parse-failed",
                  "Confirmed inconsistencies", "Unadjudicated candidates"]


def load_json(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def load_id_map(path):
    if not path:
        return {}
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            if len(row) < 2 or row[0].strip().lower() in ("filename", "file"):
                continue
            key = os.path.splitext(row[0].strip())[0]
            out[key] = row[1].strip()
    return out


def ref_evidence(r):
    bits = []
    rec = r.get("record")
    if rec:
        bits.append(f"closest/found record ({rec.get('src')}): {rec.get('title')} "
                    f"({rec.get('year')}, {rec.get('venue') or '?'})")
    for d in r.get("diffs", []):
        bits.append(f"{d[0]}: printed `{d[1]}` vs found `{d[2]}`")
    bits.extend(r.get("notes", []))
    return " | ".join(bits)[:500]


def compile_dir(dirpath, id_map):
    findings, summary = [], []
    stems = sorted({os.path.basename(j)[:-len("_ref_audit.json")]
                    for j in glob.glob(os.path.join(dirpath, "*_ref_audit.json"))} |
                   {os.path.basename(j)[:-len("_result_audit.json")]
                    for j in glob.glob(os.path.join(dirpath, "*_result_audit.json"))})
    for stem in stems:
        pid = id_map.get(stem, stem)
        fname = stem + ".pdf"
        ref = load_json(os.path.join(dirpath, f"{stem}_ref_audit.json"))
        res = load_json(os.path.join(dirpath, f"{stem}_result_audit.json"))
        adj = load_json(os.path.join(dirpath, f"{stem}_adjudication.json")) or {}
        adj_refs = adj.get("refs", {})
        adj_res = adj.get("result", {})

        n_refs = n_ver = n_hall = n_nf = n_retr = n_mm = n_pf = 0
        if ref:
            n_refs = len(ref.get("results", []))
            for r in ref.get("results", []):
                v = r["verdict"]
                a = adj_refs.get(r["key"], {})
                dec, note = a.get("decision"), a.get("note", "")
                if v == "VERIFIED":
                    n_ver += 1
                    continue
                if v == "PARSE-FAILED" and dec != "CONFIRM":
                    n_pf += 1
                    continue
                if dec == "EXCLUDE":
                    continue
                loc = f"Ref {r['key']}" + (f", p.{r['page']}" if r.get("page") else "")
                exact = (r.get("raw") or "")[:300]
                ev = ref_evidence(r)
                if note:
                    ev = (f"adjudication: {note} | " + ev)[:500]
                if v == "RETRACTED":
                    n_retr += 1
                    findings.append([pid, fname, "RETRACTED-REF", loc, exact, ev,
                                     "yes" if dec else "mechanical (DOI-based)"])
                elif v == "NOT-FOUND" or (v == "PARSE-FAILED" and dec == "CONFIRM"):
                    if dec == "CONFIRM":
                        n_hall += 1
                        findings.append([pid, fname, "HALLUCINATED-REF", loc, exact, ev, "yes"])
                    else:
                        n_nf += 1
                        findings.append([pid, fname, "NOT-FOUND-REF (unadjudicated)",
                                         loc, exact, ev, "no"])
                elif v == "MISMATCH":
                    n_mm += 1
                    findings.append([pid, fname, "MISMATCH-REF", loc, exact, ev,
                                     "yes" if dec else "no"])
                # UNVERIFIABLE / NON-PAPER-OK never become findings rows

        n_conf = n_unadj = 0
        if res:
            for c in res.get("candidates", []):
                a = adj_res.get(c["id"], {})
                if a.get("decision") == "CONFIRMED-INCONSISTENT":
                    n_conf += 1
                    ev = f"[{c['check']}] {c['ctx']}"
                    if a.get("note"):
                        ev = f"adjudication: {a['note']} | " + ev
                    findings.append([pid, fname, "DATA-INCONSISTENCY",
                                     f"p.{c['page']} ({c['id']})", c["detail"][:300],
                                     ev[:500], "yes"])
                elif not a.get("decision"):
                    n_unadj += 1

        summary.append([pid, fname, n_refs, n_ver, n_hall, n_nf, n_retr, n_mm, n_pf,
                        n_conf, n_unadj])

    sev = {"RETRACTED-REF": 0, "HALLUCINATED-REF": 1, "DATA-INCONSISTENCY": 2,
           "NOT-FOUND-REF (unadjudicated)": 3, "MISMATCH-REF": 4}
    findings.sort(key=lambda r: (r[0], sev.get(r[2], 9), r[3]))
    summary.sort(key=lambda r: (-(r[4] + r[6]), -(r[5] + r[9]), r[0]))
    return findings, summary


# ---------------- writers ----------------

def write_xlsx(findings, summary, out):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = "Findings"
    widths_f = [14, 22, 26, 18, 60, 70, 12]
    for sheet, header, rows, widths in ((ws, FINDINGS_HEADER, findings, widths_f),
                                        (wb.create_sheet("Summary"), SUMMARY_HEADER, summary,
                                         [14, 22, 10, 12, 12, 14, 10, 12, 12, 14, 14])):
        sheet.append(header)
        for c in sheet[1]:
            c.font = Font(bold=True)
        for row in rows:
            sheet.append(row)
        for i, w in enumerate(widths, 1):
            sheet.column_dimensions[get_column_letter(i)].width = w
        for row in sheet.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(vertical="top", wrap_text=True)
        sheet.freeze_panes = "A2"
    note = ("Findings are verifiable content failures with quoted evidence. They do not, "
            "by themselves, prove how the paper was written; attribution and the final "
            "decision belong to the technical chair.")
    ws2 = wb["Summary"]
    ws2.append([])
    ws2.append([f"Generated {datetime.date.today().isoformat()} by panel_compile.py. {note}"])
    wb.save(out)
    print(f"[panel] {out}  ({len(findings)} findings, {len(summary)} papers)")


def write_csv(findings, summary, out):
    base = os.path.splitext(out)[0]
    for name, header, rows in (("findings", FINDINGS_HEADER, findings),
                               ("summary", SUMMARY_HEADER, summary)):
        p = f"{base}_{name}.csv"
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
        print(f"[panel] {p}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="the audited papers directory")
    ap.add_argument("--out", default=None, help="default: <dir>/PANEL_TRIAGE.xlsx")
    ap.add_argument("--id-map", default=None, help="CSV filename,paper_id[,title]")
    args = ap.parse_args()

    out = args.out or os.path.join(args.dir, "PANEL_TRIAGE.xlsx")
    findings, summary = compile_dir(args.dir, load_id_map(args.id_map))
    if not summary:
        raise SystemExit(f"[panel] no *_ref_audit.json / *_result_audit.json in {args.dir} "
                         f"-- run the audits first")
    unadj = sum(r[10] for r in summary) + sum(r[5] for r in summary)
    if unadj:
        print(f"[panel] NOTE: {unadj} unadjudicated item(s) -- result-audit candidates are "
              f"EXCLUDED from Findings until adjudicated; NOT-FOUND refs are marked "
              f"unadjudicated. Run the adjudication pass for a final workbook.")
    try:
        write_xlsx(findings, summary, out)
    except ImportError:
        print("[panel] openpyxl not installed -- writing CSV fallback "
              "(`python -m pip install openpyxl` for .xlsx)")
        write_csv(findings, summary, out)


if __name__ == "__main__":
    main()

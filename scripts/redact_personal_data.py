#!/usr/bin/env python3
"""Redact personal contact data before public release (reproducible, idempotent).

What it changes
- public_trust_5pillars_metrics.csv: e-mail addresses, Indonesian mobile numbers
  and "Name, S.H., M.H."-style bylines in raw_evidence_snippet / metric_value are
  replaced with [EMAIL] / [PHONE] / [NAME]. The token keeps the evidence that a
  contact channel existed; mailto:/tel: links in source_url become blank.
- laptah_extracted_indicators.csv: rows where the LAPTAH parser captured an
  official's name and 18-digit NIP (and read the NIP as a budget amount) are
  marked EXTRACTION_ERROR, the label is redacted and the bogus numbers blanked.

Usage: python scripts/redact_personal_data.py [data/tables]
Prints counts; writes files in place (UTF-8, same column order).
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?:\+62|\b0)8\d{8,11}\b|(?:\+62|\b0)8\d{2,3}[-\s]\d{3,4}[-\s]\d{3,5}\b")
NIP = re.compile(r"\b\d{18}\b")
BYLINE = re.compile(
    r"\b(?:[A-Z][A-Za-z'.]*\s){1,5}[A-Z][A-Za-z'.]*,\s*(?:S\.[A-Za-z]+\.?,?\s*)+(?:M\.[A-Za-z.]+\.?)?"
)


def scrub(text: str) -> tuple[str, int]:
    n = 0
    for rx, tok in ((EMAIL, "[EMAIL]"), (PHONE, "[PHONE]"), (NIP, "[NIP]"), (BYLINE, "[NAME]")):
        text, k = rx.subn(tok, text)
        n += k
    return text, n


def rewrite(path: Path, fn) -> int:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        rows = list(reader)
    changed = sum(fn(r) for r in rows)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return changed


def pillars(row: dict) -> int:
    n = 0
    for col in ("raw_evidence_snippet", "metric_value"):
        if row.get(col):
            row[col], k = scrub(row[col])
            n += k
    url = row.get("source_url", "")
    if url.lower().startswith(("mailto:", "tel:")) or EMAIL.search(url) or PHONE.search(url):
        row["source_url"] = ""
        n += 1
    return n


def laptah(row: dict) -> int:
    if not any(NIP.search(v or "") for v in row.values()):
        return 0
    row["indicator_label"] = "[REDACTED: official's name/NIP captured by extraction error]"
    for col in ("sisa_awal", "masuk", "beban", "putus", "sisa_akhir", "pagu_idr",
                "realisasi_idr", "sisa_pagu_idr", "realisasi_pct", "count_value"):
        if col in row:
            row[col] = ""
    row["extraction_status"] = "EXTRACTION_ERROR"
    return 1


def main() -> int:
    base = Path(sys.argv[1] if len(sys.argv) > 1 else "data/tables")
    p1 = base / "public_trust_5pillars_metrics.csv"
    p2 = base / "laptah_extracted_indicators.csv"
    print(f"{p1.name}: {rewrite(p1, pillars)} redactions")
    print(f"{p2.name}: {rewrite(p2, laptah)} rows marked EXTRACTION_ERROR")
    return 0


if __name__ == "__main__":
    sys.exit(main())

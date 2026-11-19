#!/usr/bin/env python3
"""Fix source references that are not public URLs (reproducible, idempotent).

- public_trust_5pillars_metrics.csv: rows whose source_url is not a resolvable
  http(s) URL (e.g. "javascript:void(0)", "javascript:;", "https://-") were
  labelled VERIFIED_PORTAL_LINK although no link target was captured. The fake
  URL is blanked and verification_status becomes UNVERIFIED.
- source_register.csv: file:// paths to locally held signed PDFs are moved into
  the notes column; source_url is left blank (the decree is identified by
  document_reference and relevant_page).

Usage: python scripts/fix_invalid_source_urls.py [data/tables]
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

URL_RE = re.compile(r"^https?://[^\s/$.?#].[^\s]*$")
SPACED_RE = re.compile(r"^https?://\S+\s")


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
    url = (row.get("source_url") or "").strip()
    if not url or URL_RE.match(url) or SPACED_RE.match(url):
        return 0
    row["source_url"] = ""
    row["verification_status"] = "UNVERIFIED"
    return 1


def register(row: dict) -> int:
    url = row.get("source_url") or ""
    if "file://" not in url:
        return 0
    files = "; ".join(p.strip().removeprefix("file://") for p in url.split(";") if p.strip())
    row["notes"] = (row.get("notes") or "").rstrip() + f" Source documents: signed PDF copies held offline ({files})."
    row["source_url"] = ""
    return 1


def main() -> int:
    base = Path(sys.argv[1] if len(sys.argv) > 1 else "data/tables")
    print(f"public_trust_5pillars_metrics.csv: {rewrite(base / 'public_trust_5pillars_metrics.csv', pillars)} rows fixed")
    print(f"source_register.csv: {rewrite(base / 'source_register.csv', register)} rows fixed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

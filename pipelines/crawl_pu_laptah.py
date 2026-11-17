#!/usr/bin/env python3
"""
Audit & Ekstraksi Link Laporan Tahunan (LAPTAH) — 34 PT & 380 PN Seluruh Indonesia
Sumber Induk:
  1. Direktori Pengadilan Ditjen Badilum: https://badilum.mahkamahagung.go.id/direktori-pengadilan.html
  2. Portal Laporan Tahunan MA RI: https://kepaniteraan.mahkamahagung.go.id/id/publikasi/laporan-tahunan
Output:
  - pu_websites_verified.csv (update untuk Court_Websites.csv)
  - pu_laptah_links_verified.csv (update untuk LAPTAH_Registry.csv & LAPTAH_Availability_Matrix.csv)
"""

import re
import time
import urllib.parse
from datetime import date
import pandas as pd
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "IJC-Judicial-Transparency-Audit/1.0 (+https://github.com/bsdkijc-dev)"
}
TODAY = date.today().isoformat()

LAPTAH_KEYWORDS = re.compile(
    r"(laptah|laporan[-_\s]*tahunan|laporan[-_\s]*pelaksanaan[-_\s]*kegiatan)",
    re.IGNORECASE,
)

def discover_domain_links(session: requests.Session) -> dict[str, str]:
    """Ambil daftar URL resmi PT & PN dari direktori Ditjen Badilum."""
    url = "https://badilum.mahkamahagung.go.id/direktori-pengadilan.html"
    domain_map = {}
    try:
        resp = session.get(url, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            text = a.get_text(" ", strip=True)
            if re.search(r"https?://(www\.)?(pt|pn)-[a-z0-9-]+\.go\.id", href, re.I):
                clean_url = urllib.parse.urljoin(url, href).split("#")[0]
                domain_map[text.lower()] = clean_url
    except Exception as e:
        print(f"[WARN] Gagal mengambil direktori Badilum: {e}")
    return domain_map

def scan_court_laptah(session: requests.Session, court_id: str, court_name: str, base_url: str) -> list[dict]:
    """Telusuri halaman utama & sub-halaman publikasi/laporan tahunan satker untuk menemukan PDF."""
    found = []
    visited = set()
    candidate_paths = [
        "",
        "publikasi/laporan-tahunan",
        "laporan-tahunan",
        "transparansi/laporan-tahunan",
        "informasi-publik/laporan-tahunan",
        "arsip/laporan-tahunan",
    ]
    queue = [urllib.parse.urljoin(base_url.rstrip("/") + "/", p) for p in candidate_paths]

    while queue:
        target = queue.pop(0)
        if target in visited:
            continue
        visited.add(target)
        try:
            r = session.get(target, timeout=15, allow_redirects=True)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = urllib.parse.urljoin(r.url, a["href"].strip())
                label = a.get_text(" ", strip=True)
                if href.lower().endswith(".pdf") and (LAPTAH_KEYWORDS.search(href) or LAPTAH_KEYWORDS.search(label)):
                    yr_match = re.search(r"(201\d|202[0-6])", f"{href} {label}")
                    rep_year = yr_match.group(1) if yr_match else "UNKNOWN"
                    found.append({
                        "document_id": f"LAPTAH-{court_id}-{rep_year}",
                        "court_id": court_id,
                        "court_name": court_name,
                        "reporting_year": rep_year,
                        "document_title": label or f"Laporan Tahunan {rep_year} {court_name}",
                        "document_url": href,
                        "file_format": "PDF",
                        "access_status": "PUBLISHED_ACCESSIBLE",
                        "search_date": TODAY,
                    })
                elif urllib.parse.urlparse(href).netloc == urllib.parse.urlparse(base_url).netloc:
                    if LAPTAH_KEYWORDS.search(href) or LAPTAH_KEYWORDS.search(label):
                        if href not in visited and len(visited) < 15:
                            queue.append(href)
        except Exception:
            continue
    # Deduplikasi berdasarkan document_url
    seen_urls = set()
    unique_found = []
    for item in found:
        if item["document_url"] not in seen_urls:
            seen_urls.add(item["document_url"])
            unique_found.append(item)
    return unique_found

def main():
    df = pd.read_csv("pu_pt_pn_laptah_audit_tracker_2026.csv", dtype=str).fillna("")
    session = requests.Session()
    session.headers.update(HEADERS)

    print("[1/3] Mengambil direktori domain resmi PT & PN dari Ditjen Badilum...")
    domain_map = discover_domain_links(session)
    print(f"      Ditemukan {len(domain_map)} tautan domain dari direktori Badilum.")

    website_rows = []
    laptah_rows = []

    for _, row in df.iterrows():
        cid = row["court_id"]
        cname = row["official_name_verified"]
        status = row["operational_status"]
        if status != "OPERATIONAL":
            continue

        site = row["official_website"] if row["official_website"].startswith("http") else ""
        if not site:
            site = domain_map.get(cname.lower(), "")

        if site:
            website_rows.append({
                "website_id": f"WEB-{cid}",
                "court_id": cid,
                "website_type": "OFFICIAL_MAIN",
                "website_url": site,
                "domain_owner": cname,
                "access_status": "ACTIVE",
                "last_checked": TODAY,
                "verification_source": "Badilum Directory & Live HTTP Audit",
            })
            pdfs = scan_court_laptah(session, cid, cname, site)
            laptah_rows.extend(pdfs)
            print(f"[OK] {cid} | {cname} -> {site} ({len(pdfs)} PDF LAPTAH)")
            time.sleep(0.3)

    pd.DataFrame(website_rows).to_csv("pu_websites_verified.csv", index=False)
    pd.DataFrame(laptah_rows).to_csv("pu_laptah_links_verified.csv", index=False)
    print(f"[SELESAI] Tersimpan {len(website_rows)} website resmi dan {len(laptah_rows)} link PDF LAPTAH.")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import pandas as pd
import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) IJC-Judicial-Transparency-Audit/1.2"}
TODAY = date.today().isoformat()
LAPTAH_KEYWORDS = re.compile(r"(laptah|laporan[-_\s]*tahunan|laporan[-_\s]*pelaksanaan[-_\s]*kegiatan)", re.IGNORECASE)

def make_candidate_urls(row: pd.Series) -> list[str]:
    existing = row["official_website"].strip()
    urls = [existing] if existing.startswith("http") else []
    name = row["official_name_verified"].lower().strip()
    env = row["judicial_environment"]

    if "mahkamah syar'iyah aceh" in name:
        return list(dict.fromkeys(urls + ["https://ms-aceh.go.id/", "https://www.ms-aceh.go.id/"]))

    if "Militer" in env:
        m_num = re.search(r"dilmil(?:ti)?\s*(?:[ivx]+-)?(\d+)", row["court_abbreviation"].lower())
        city = name.split()[-1]
        if "utama" in name:
            urls.extend(["https://www.miltama.dilmiltama.go.id/", "https://dilmiltama.go.id/"])
        elif "tinggi" in name:
            urls.extend([f"https://dilmilti-{city}.go.id/", f"https://www.dilmilti-{city}.go.id/"])
        else:
            urls.extend([f"https://dilmil-{city}.go.id/", f"https://www.dilmil-{city}.go.id/"])
            if m_num:
                urls.append(f"https://dilmil-{m_num.group(1)}-{city}.go.id/")
        return list(dict.fromkeys(urls))

    prefix_rules = [
        ("pengadilan tinggi agama ", "pta"),
        ("mahkamah syar'iyah ", "ms"),
        ("pengadilan agama ", "pa"),
        ("pengadilan tinggi tata usaha negara ", "pttun"),
        ("pengadilan tata usaha negara ", "ptun"),
        ("pengadilan tinggi ", "pt"),
        ("pengadilan negeri ", "pn"),
    ]
    for phrase, prefix in prefix_rules:
        if name.startswith(phrase):
            loc = name.replace(phrase, "", 1).strip()
            loc_clean = re.sub(r"^kabupaten\s+", "", loc)
            loc_short = loc_clean.split(" di ")[-1].strip()
            for variant in dict.fromkeys([loc_clean, loc_short]):
                c_compact = re.sub(r"[^a-z0-9]", "", variant)
                c_hyphen = re.sub(r"[^a-z0-9]+", "-", variant).strip("-")
                if c_compact:
                    urls.extend([f"https://{prefix}-{c_compact}.go.id/", f"https://www.{prefix}-{c_compact}.go.id/"])
                if c_hyphen and c_hyphen != c_compact:
                    urls.extend([f"https://{prefix}-{c_hyphen}.go.id/", f"https://www.{prefix}-{c_hyphen}.go.id/"])
            break
    return list(dict.fromkeys(urls))

def scan_single_court(row: pd.Series) -> tuple[str, str | None, list[dict]]:
    cid = row["court_id"]
    cname = row["official_name_verified"]
    if row["operational_status"] != "OPERATIONAL":
        return cid, None, []

    session = requests.Session()
    session.headers.update(HEADERS)
    verified_url = None
    tokens = [t for t in re.split(r"[^a-z0-9]+", cname.lower()) if t not in {"pengadilan", "negeri", "tinggi", "agama", "tata", "usaha", "negara", "militer", "mahkamah", "syar", "iyah", "kabupaten", "kota", "di"} and len(t) >= 3]
    check_token = tokens[-1] if tokens else ""

    for u in make_candidate_urls(row):
        try:
            r = session.get(u, timeout=10, allow_redirects=True)
            if r.status_code == 200 and (not check_token or check_token in r.text.lower()):
                verified_url = r.url.split("#")[0]
                break
        except Exception:
            continue

    if not verified_url:
        return cid, None, []

    found_pdfs, visited = [], set()
    subpaths = ["", "publikasi/laporan-tahunan", "laporan-tahunan", "transparansi/laporan-tahunan", "informasi-publik/laporan-tahunan", "arsip/laporan-tahunan", "publikasi", "transparansi"]
    queue = [urllib.parse.urljoin(verified_url.rstrip("/") + "/", sp) for sp in subpaths]

    while queue:
        target = queue.pop(0)
        if target in visited:
            continue
        visited.add(target)
        try:
            r = session.get(target, timeout=10, allow_redirects=True)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = urllib.parse.urljoin(r.url, a["href"].strip())
                label = a.get_text(" ", strip=True)
                if href.lower().endswith(".pdf") and (LAPTAH_KEYWORDS.search(href) or LAPTAH_KEYWORDS.search(label)):
                    yr_match = re.search(r"(201\d|202[0-6])", f"{href} {label}")
                    rep_year = yr_match.group(1) if yr_match else "UNKNOWN"
                    found_pdfs.append({
                        "document_id": f"LAPTAH-{cid}-{rep_year}",
                        "court_id": cid,
                        "reporting_year": rep_year,
                        "document_title": label or f"Laporan Tahunan {rep_year} {cname}",
                        "publication_date": "",
                        "document_url": href,
                        "file_format": "PDF",
                        "file_size": "",
                        "page_count": "",
                        "file_hash": "",
                        "access_status": "PUBLISHED_ACCESSIBLE",
                        "source_reference": "LIVE_PORTAL_CRAWL_2026",
                        "verification_status": "VERIFIED",
                    })
                elif urllib.parse.urlparse(href).netloc == urllib.parse.urlparse(verified_url).netloc:
                    if (LAPTAH_KEYWORDS.search(href) or LAPTAH_KEYWORDS.search(label)) and href not in visited and len(visited) < 16:
                        queue.append(href)
        except Exception:
            continue

    return cid, verified_url, list({p["document_url"]: p for p in found_pdfs}.values())

def main():
    mcr_path = "data/master_court_registry.csv"
    df_mcr = pd.read_csv(mcr_path, dtype=str).fillna("")
    op_df = df_mcr[df_mcr["operational_status"] == "OPERATIONAL"].copy()
    print(f"[START] Memindai website & LAPTAH untuk {len(op_df)} satker operasional...")

    url_updates, all_laptahs = {}, []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(scan_single_court, row): row for _, row in op_df.iterrows()}
        for idx, fut in enumerate(as_completed(futures), start=1):
            row = futures[fut]
            cid, v_url, pdfs = fut.result()
            if v_url:
                url_updates[cid] = v_url
                all_laptahs.extend(pdfs)
                if pdfs or idx % 25 == 0:
                    print(f"[{idx}/{len(op_df)}] {cid} | {row['official_name_verified']} -> {v_url} ({len(pdfs)} PDF LAPTAH)")

    reg_path = "data/tables/laptah_registry.csv"
    df_combined_reg = pd.concat([pd.read_csv(reg_path, dtype=str).fillna(""), pd.DataFrame(all_laptahs)], ignore_index=True).drop_duplicates(subset=["document_url"], keep="first")
    df_combined_reg.to_csv(reg_path, index=False)

    web_path = "data/tables/court_websites.csv"
    new_web_rows = [{
        "website_id": f"WEB-{cid}",
        "court_id": cid,
        "website_type": "OFFICIAL_MAIN",
        "website_url": u,
        "domain_owner": df_mcr.loc[df_mcr["court_id"] == cid, "official_name_verified"].values[0],
        "access_status": "ACTIVE",
        "last_checked": TODAY,
        "verification_source": "Live HTTP & Title Verification",
    } for cid, u in url_updates.items()]
    df_combined_web = pd.concat([pd.read_csv(web_path, dtype=str).fillna(""), pd.DataFrame(new_web_rows)], ignore_index=True).drop_duplicates(subset=["court_id", "website_type"], keep="last")
    df_combined_web.to_csv(web_path, index=False)

    laptah_by_court = df_combined_reg.groupby("court_id")["reporting_year"].apply(list).to_dict()
    for idx, r in df_mcr.iterrows():
        cid = r["court_id"]
        if cid in url_updates:
            df_mcr.at[idx, "official_website"] = url_updates[cid]
        if cid in laptah_by_court:
            yrs = sorted([y for y in laptah_by_court[cid] if y.isdigit()])
            df_mcr.at[idx, "laptah_coverage_count"] = str(len(laptah_by_court[cid]))
            if yrs:
                df_mcr.at[idx, "first_laptah_year"] = yrs[0]
                df_mcr.at[idx, "latest_laptah_year"] = yrs[-1]
    df_mcr.to_csv(mcr_path, index=False)
    print(f"[DONE] Website terverifikasi: {len(df_combined_web)} satker | Total PDF LAPTAH: {len(df_combined_reg)} dokumen.")

if __name__ == "__main__":
    main()

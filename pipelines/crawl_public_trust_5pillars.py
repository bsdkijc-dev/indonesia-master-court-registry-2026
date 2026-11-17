#!/usr/bin/env python3
"""
Crawler Terpadu 5 Pilar Statistik Kepercayaan Publik Peradilan Indonesia
Membaca `data/tables/court_websites.csv` (651 website satker terverifikasi) & memanen:
  1. IKM_IPAK_SURVEY        : Skor Indeks Kepuasan Masyarakat & Indeks Persepsi Anti Korupsi
  2. DECISION_PUBLICATION   : Ketersediaan & Statistik Direktori Putusan / One Day Publish
  3. SIPP_ECOURT_METRICS    : Rasio Penyelesaian SIPP & Statistik Perkara e-Court / e-Litigation
  4. SIWAS_COMPLAINTS       : Kanal & Rekapitulasi Pengaduan Masyarakat / SIWAS / Bawas
  5. PPID_VULNERABLE_ACCESS : Kepatuhan e-PPID, Posbakum, Prodeo, Sidang Keliling & Disabilitas

Output:
  - data/tables/public_trust_5pillars_metrics.csv (seluruh temuan terverifikasi per satker & pilar)
  - data/tables/public_trust_court_scorecard.csv (matriks kesiapan & skor keterbukaan per satker)
"""

import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import urllib3
import pandas as pd
import requests
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TODAY = date.today().isoformat()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) IJC-PublicTrust-Audit/1.0 (+https://github.com/bsdkijc-dev)"
}

WEB_PATH = "data/tables/court_websites.csv"
MCR_PATH = "data/master_court_registry.csv"
OUT_METRICS_PATH = "data/tables/public_trust_5pillars_metrics.csv"
OUT_SCORECARD_PATH = "data/tables/public_trust_court_scorecard.csv"

# Definisi jalur sub-halaman kandidat untuk setiap pilar pada portal satker
PILLAR_PATHS = {
    "1_IKM_IPAK_SURVEY": [
        "survei", "survey", "ikm", "ipak", "survei-kepuasan-masyarakat",
        "indeks-kepuasan-masyarakat", "ptsp", "layanan-publik/survei",
    ],
    "2_DECISION_PUBLICATION": [
        "putusan", "direktori-putusan", "one-day-publish", "one-day-minutation",
        "informasi-perkara/putusan", "transparansi/putusan",
    ],
    "3_SIPP_ECOURT_METRICS": [
        "sipp", "statistik-perkara", "ecourt", "e-court", "e-litigation",
        "informasi-perkara", "kinerja-perkara",
    ],
    "4_SIWAS_COMPLAINTS": [
        "pengaduan", "siwas", "whistleblowing", "pengawasan",
        "layanan-pengaduan", "mekanisme-pengaduan",
    ],
    "5_PPID_VULNERABLE_ACCESS": [
        "ppid", "eppid", "e-ppid", "informasi-publik", "daftar-informasi-publik",
        "posbakum", "prodeo", "sidang-keliling", "disabilitas", "layanan-disabilitas",
    ],
}

# Pola regex ekstraksi metrik kuantitatif terverifikasi (anti-fabrikasi)
REGEX_RULES = {
    "1_IKM_IPAK_SURVEY": [
        ("IKM_SCORE", re.compile(r"(?:indeks\s+kepuasan\s+masyarakat|nilai\s+ikm|skor\s+ikm)[^\d]{0,35}(\d{2}[.,]\d{1,2}|\d[.,]\d{2})", re.I)),
        ("IPAK_SCORE", re.compile(r"(?:persepsi\s+anti\s+korupsi|nilai\s+ipak|skor\s+ipak)[^\d]{0,35}(\d{2}[.,]\d{1,2}|\d[.,]\d{2})", re.I)),
        ("RESPONDENT_COUNT", re.compile(r"(?:jumlah\s+responden|total\s+responden)[^\d]{0,25}(\d{1,3}(?:[.,]\d{3})*|\d+)", re.I)),
    ],
    "2_DECISION_PUBLICATION": [
        ("PUBLISHED_DECISIONS_COUNT", re.compile(r"(?:putusan\s+(?:terpublikasi|diunggah|upload)|jumlah\s+putusan)[^\d]{0,30}(\d{1,3}(?:[.,]\d{3})+|\d{2,6})", re.I)),
        ("ONE_DAY_PUBLISH_PCT", re.compile(r"(?:one\s+day\s+(?:publish|minutation))[^\d]{0,35}(\d{2,3}(?:[.,]\d{1,2})?)\s*%", re.I)),
    ],
    "3_SIPP_ECOURT_METRICS": [
        ("CLEARANCE_RATE_PCT", re.compile(r"(?:rasio\s+(?:penyelesaian|minutasi)|kinerja\s+penyelesaian)[^\d]{0,35}(\d{2,3}(?:[.,]\d{1,2})?)\s*%", re.I)),
        ("ECOURT_CASE_COUNT", re.compile(r"(?:perkara\s+e-?court|pendaftaran\s+e-?court|e-?litigation)[^\d]{0,30}(\d{1,3}(?:[.,]\d{3})*|\d+)", re.I)),
    ],
    "4_SIWAS_COMPLAINTS": [
        ("COMPLAINT_COUNT", re.compile(r"(?:jumlah\s+pengaduan|pengaduan\s+masuk|laporan\s+pengaduan)[^\d]{0,30}(\d{1,3}(?:[.,]\d{3})*|\d+)", re.I)),
        ("COMPLAINT_RESOLVED_PCT", re.compile(r"(?:pengaduan\s+(?:selesai|ditindaklanjuti))[^\d]{0,35}(\d{2,3}(?:[.,]\d{1,2})?)\s*%", re.I)),
    ],
    "5_PPID_VULNERABLE_ACCESS": [
        ("PPID_REQUEST_COUNT", re.compile(r"(?:permohonan\s+informasi|pemohon\s+informasi)[^\d]{0,30}(\d{1,3}(?:[.,]\d{3})*|\d+)", re.I)),
        ("PRODEO_POSBAKUM_COUNT", re.compile(r"(?:layanan\s+posbakum|perkara\s+prodeo|pembebasan\s+biaya|sidang\s+(?:keliling|di\s+luar\s+gedung))[^\d]{0,30}(\d{1,3}(?:[.,]\d{3})*|\d+)", re.I)),
    ],
}

LINK_DISCOVERY_KEYWORDS = {
    "1_IKM_IPAK_SURVEY": re.compile(r"(ikm|ipak|survei|survey|kepuasan)", re.I),
    "2_DECISION_PUBLICATION": re.compile(r"(putusan3|direktori[\s_-]*putusan|one[\s_-]*day)", re.I),
    "3_SIPP_ECOURT_METRICS": re.compile(r"(sipp\.|ecourt|e-court|statistik[\s_-]*perkara)", re.I),
    "4_SIWAS_COMPLAINTS": re.compile(r"(siwas|pengaduan|whistleblowing|bawas)", re.I),
    "5_PPID_VULNERABLE_ACCESS": re.compile(r"(eppid|ppid|posbakum|prodeo|disabilitas|sidang[\s_-]*keliling)", re.I),
}


def fetch_html(session: requests.Session, url: str) -> tuple[str, str]:
    try:
        r = session.get(url, timeout=10, allow_redirects=True, verify=False)
        if r.status_code == 200 and "text/html" in r.headers.get("Content-Type", "").lower():
            return r.url.split("#")[0], r.text
    except Exception:
        pass
    return "", ""


def crawl_court_5pillars(row: pd.Series) -> tuple[list[dict], dict]:
    cid = row["court_id"]
    cname = row["domain_owner"]
    base_url = row["website_url"].strip()

    session = requests.Session()
    session.headers.update(HEADERS)

    findings = []
    pillar_status = {p: False for p in PILLAR_PATHS}
    visited = set()

    # 1. Pindai halaman utama untuk menemukan menu/tautan internal & eksternal (SIPP, Direktori Putusan, SIWAS, e-PPID)
    final_home, home_html = fetch_html(session, base_url)
    if not final_home:
        return [], {
            "court_id": cid,
            "official_name_verified": cname,
            "website_url": base_url,
            "reachable": "UNREACHABLE",
            **{f"has_{p}": "0" for p in PILLAR_PATHS},
            "pillars_detected_count": "0",
            "extracted_metrics_count": "0",
            "audit_date": TODAY,
        }

    visited.add(final_home)
    discovered_urls = {p: [] for p in PILLAR_PATHS}

    soup_home = BeautifulSoup(home_html, "lxml")
    for a in soup_home.find_all("a", href=True):
        href = urllib.parse.urljoin(final_home, a["href"].strip())
        lbl = a.get_text(" ", strip=True)
        combo = f"{href} {lbl}"
        for pillar, kw_re in LINK_DISCOVERY_KEYWORDS.items():
            if kw_re.search(combo):
                pillar_status[pillar] = True
                if urllib.parse.urlparse(href).netloc == urllib.parse.urlparse(final_home).netloc:
                    if href not in discovered_urls[pillar] and len(discovered_urls[pillar]) < 3:
                        discovered_urls[pillar].append(href)
                else:
                    # Catat tautan portal terintegrasi resmi (mis. sipp.pn-xxx.go.id, putusan3, siwas, eppid)
                    findings.append({
                        "court_id": cid,
                        "official_name_verified": cname,
                        "pillar": pillar,
                        "metric_code": "INTEGRATED_PORTAL_LINK",
                        "metric_value": lbl[:80] if lbl else href,
                        "unit": "URL",
                        "source_url": href,
                        "raw_evidence_snippet": f"Anchor '{lbl[:60]}' -> {href}"[:180],
                        "verification_status": "VERIFIED_PORTAL_LINK",
                        "crawl_date": TODAY,
                    })

    # 2. Pindai sub-portal SIPP khusus satker (sipp.<domain>) jika domain utama berpola <prefix>-<kota>.go.id
    parsed_host = urllib.parse.urlparse(final_home).netloc.replace("www.", "")
    if parsed_host.endswith(".go.id") and not parsed_host.startswith("sipp."):
        sipp_url = f"https://sipp.{parsed_host}/"
        s_final, s_html = fetch_html(session, sipp_url)
        if s_final:
            pillar_status["3_SIPP_ECOURT_METRICS"] = True
            discovered_urls["3_SIPP_ECOURT_METRICS"].insert(0, s_final)
            findings.append({
                "court_id": cid,
                "official_name_verified": cname,
                "pillar": "3_SIPP_ECOURT_METRICS",
                "metric_code": "SIPP_SUBDOMAIN_ACTIVE",
                "metric_value": "ACTIVE",
                "unit": "STATUS",
                "source_url": s_final,
                "raw_evidence_snippet": f"Live SIPP Web Portal detected at {s_final}",
                "verification_status": "VERIFIED_HTTP_200",
                "crawl_date": TODAY,
            })

    # 3. Kunjungi halaman kandidat per pilar & ekstrak metrik numerik tervalidasi
    for pillar, paths in PILLAR_PATHS.items():
        targets = list(discovered_urls[pillar])
        for sp in paths[:4]:
            cand = urllib.parse.urljoin(final_home.rstrip("/") + "/", sp)
            if cand not in targets:
                targets.append(cand)

        for target_url in targets[:5]:
            if target_url in visited:
                continue
            visited.add(target_url)
            t_final, t_html = fetch_html(session, target_url)
            if not t_final:
                continue

            pillar_status[pillar] = True
            text_clean = re.sub(r"\s+", " ", BeautifulSoup(t_html, "lxml").get_text(" ", strip=True))

            # Catat keberadaan halaman pilar terverifikasi
            findings.append({
                "court_id": cid,
                "official_name_verified": cname,
                "pillar": pillar,
                "metric_code": "PILLAR_PAGE_VERIFIED",
                "metric_value": "PUBLISHED",
                "unit": "STATUS",
                "source_url": t_final,
                "raw_evidence_snippet": text_clean[:160],
                "verification_status": "VERIFIED_HTTP_200",
                "crawl_date": TODAY,
            })

            # Jalankan ekstraksi angka spesifik pilar
            for m_code, pat in REGEX_RULES[pillar]:
                for m in pat.finditer(text_clean):
                    val_raw = m.group(1).replace(".", "").replace(",", ".")
                    try:
                        val_num = float(val_raw)
                        # Validasi batas kewajaran angka agar tidak salah tangkap tahun/kode pos
                        if "PCT" in m_code and not (0 < val_num <= 100):
                            continue
                        if "SCORE" in m_code and not (1 <= val_num <= 100):
                            continue
                        if "COUNT" in m_code and (val_num <= 0 or val_num in {2020, 2021, 2022, 2023, 2024, 2025, 2026}):
                            continue
                        start_idx = max(0, m.start() - 30)
                        end_idx = min(len(text_clean), m.end() + 40)
                        findings.append({
                            "court_id": cid,
                            "official_name_verified": cname,
                            "pillar": pillar,
                            "metric_code": m_code,
                            "metric_value": str(val_num),
                            "unit": "PERCENT" if "PCT" in m_code else ("SCORE" if "SCORE" in m_code else "COUNT"),
                            "source_url": t_final,
                            "raw_evidence_snippet": text_clean[start_idx:end_idx],
                            "verification_status": "VERIFIED_REGEX_BOUND",
                            "crawl_date": TODAY,
                        })
                        break
                    except ValueError:
                        continue

    df_f = pd.DataFrame(findings).drop_duplicates(subset=["court_id", "pillar", "metric_code", "source_url"]) if findings else pd.DataFrame()
    dedup_findings = df_f.to_dict(orient="records") if not df_f.empty else []

    active_pillars = sum(1 for v in pillar_status.values() if v)
    scorecard = {
        "court_id": cid,
        "official_name_verified": cname,
        "website_url": final_home,
        "reachable": "REACHABLE",
        "has_1_ikm_ipak": "1" if pillar_status["1_IKM_IPAK_SURVEY"] else "0",
        "has_2_putusan": "1" if pillar_status["2_DECISION_PUBLICATION"] else "0",
        "has_3_sipp_ecourt": "1" if pillar_status["3_SIPP_ECOURT_METRICS"] else "0",
        "has_4_siwas": "1" if pillar_status["4_SIWAS_COMPLAINTS"] else "0",
        "has_5_ppid_rentan": "1" if pillar_status["5_PPID_VULNERABLE_ACCESS"] else "0",
        "pillars_detected_count": str(active_pillars),
        "extracted_metrics_count": str(len(dedup_findings)),
        "audit_date": TODAY,
    }
    return dedup_findings, scorecard


def main():
    df_web = pd.read_csv(WEB_PATH, dtype=str).fillna("")
    df_web = df_web[df_web["website_url"].str.startswith("http")].drop_duplicates(subset=["court_id"])
    print(f"[START] Memulai crawling terpadu 5 Pilar Kepercayaan Publik pada {len(df_web)} satker terverifikasi...")

    all_metrics = []
    all_scorecards = []

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(crawl_court_5pillars, row): row for _, row in df_web.iterrows()}
        for idx, fut in enumerate(as_completed(futures), start=1):
            metrics, card = fut.result()
            all_metrics.extend(metrics)
            all_scorecards.append(card)
            if idx % 25 == 0 or idx == len(df_web):
                print(f"[{idx}/{len(df_web)}] Satker dipindai | Akumulasi metrik & bukti: {len(all_metrics)} baris")

    df_metrics = pd.DataFrame(all_metrics).drop_duplicates()
    df_metrics.to_csv(OUT_METRICS_PATH, index=False)

    # Gabungkan scorecard dengan metadata master_court_registry.csv
    df_mcr = pd.read_csv(MCR_PATH, dtype=str).fillna("")[["court_id", "judicial_environment", "province_name", "court_level"]]
    df_cards = pd.DataFrame(all_scorecards).merge(df_mcr, on="court_id", how="left")
    df_cards.to_csv(OUT_SCORECARD_PATH, index=False)

    print("\n=== RINGKASAN CRAWL 5 PILAR KEPERCAYAAN PUBLIK ===")
    if not df_metrics.empty:
        print(df_metrics.groupby(["pillar", "metric_code"]).size().to_string())
    print(f"\n[DONE] Tersimpan di {OUT_METRICS_PATH} ({len(df_metrics)} baris) & {OUT_SCORECARD_PATH} ({len(df_cards)} satker).")


if __name__ == "__main__":
    main()

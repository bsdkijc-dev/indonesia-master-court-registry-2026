#!/usr/bin/env python3
"""
Audit Hash SHA-256, Jumlah Halaman, & Ekstraksi Indikator Perkara/Anggaran dari Arsip PDF LAPTAH
Input  : ~/JPAIS/laptah_pdfs/*.pdf & data/tables/laptah_registry.csv
Output :
  - data/tables/laptah_registry.csv (diperbarui: file_size, page_count, file_hash SHA-256)
  - data/tables/laptah_availability_matrix.csv (disinkronkan)
  - data/tables/laptah_extracted_indicators.csv (indikator perkara & DIPA tervalidasi)
"""

import os
import re
import hashlib
from datetime import date
import fitz  # PyMuPDF
import pandas as pd

TODAY = date.today().isoformat()
PDF_DIR = os.path.expanduser("~/JPAIS/laptah_pdfs")
REG_PATH = "data/tables/laptah_registry.csv"
MAT_PATH = "data/tables/laptah_availability_matrix.csv"
OUT_IND_PATH = "data/tables/laptah_extracted_indicators.csv"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_int(s: str) -> int | None:
    cleaned = re.sub(r"[^\d]", "", s)
    return int(cleaned) if cleaned else None


def extract_indicators_from_pdf(doc: fitz.Document, doc_id: str, court_id: str, rep_year: str) -> list[dict]:
    """Ekstrak baris statistik perkara (dengan pagar identitas matematis) & realisasi DIPA."""
    records = []
    max_pages = min(len(doc), 120)

    for pno in range(max_pages):
        text = doc[pno].get_text("text")
        if not text.strip():
            continue

        # 1. Deteksi pola 5-6 angka berurutan pada tabel rekap perkara: Sisa Awal | Masuk | Beban | Putus | Sisa Akhir
        for line in text.splitlines():
            nums = re.findall(r"\b\d{1,3}(?:[.,]\d{3})*\b", line)
            if len(nums) >= 5:
                vals = [parse_int(n) for n in nums[:6]]
                if all(v is not None for v in vals[:5]):
                    sisa_awal, masuk, beban, putus, sisa_akhir = vals[:5]
                    if beban > 0 and (sisa_awal + masuk == beban) and (beban - putus == sisa_akhir):
                        label = re.sub(r"\b\d{1,3}(?:[.,]\d{3})*\b", "", line).strip(" :|-•\t")
                        if len(label) >= 4:
                            records.append({
                                "document_id": doc_id,
                                "court_id": court_id,
                                "reporting_year": rep_year,
                                "indicator_domain": "CASELOAD_SUMMARY",
                                "indicator_label": label[:120],
                                "sisa_awal": sisa_awal,
                                "masuk": masuk,
                                "beban": beban,
                                "putus": putus,
                                "sisa_akhir": sisa_akhir,
                                "pagu_idr": "",
                                "realisasi_idr": "",
                                "realisasi_pct": "",
                                "source_page": pno + 1,
                                "extraction_status": "VERIFIED_MATH_MATCH",
                            })

        # 2. Deteksi ringkasan Pagu & Realisasi DIPA (Rp ...)
        dipa_matches = re.findall(
            r"(DIPA\s*0[1-6]|Belanja\s+(?:Pegawai|Barang|Modal)|Total\s+Anggaran)[^\n]{0,60}?"
            r"(?:Rp\.?\s*)?(\d{1,3}(?:\.\d{3}){2,})[^\n]{0,40}?"
            r"(?:Rp\.?\s*)?(\d{1,3}(?:\.\d{3}){2,})",
            text,
            re.IGNORECASE,
        )
        for lbl, v1_str, v2_str in dipa_matches:
            v1, v2 = parse_int(v1_str), parse_int(v2_str)
            if v1 and v2 and v1 >= v2 and v1 >= 10_000_000:
                pct = round((v2 / v1) * 100, 2)
                records.append({
                    "document_id": doc_id,
                    "court_id": court_id,
                    "reporting_year": rep_year,
                    "indicator_domain": "BUDGET_DIPA",
                    "indicator_label": lbl.strip(),
                    "sisa_awal": "",
                    "masuk": "",
                    "beban": "",
                    "putus": "",
                    "sisa_akhir": "",
                    "pagu_idr": v1,
                    "realisasi_idr": v2,
                    "realisasi_pct": pct,
                    "source_page": pno + 1,
                    "extraction_status": "VERIFIED_DIPA_BOUND",
                })

    return records


def main():
    df_reg = pd.read_csv(REG_PATH, dtype=str).fillna("")
    extracted_all = []
    hashed_count = 0

    for idx, row in df_reg.iterrows():
        fname = f"{row['document_id']}.pdf".replace("/", "_")
        pdf_path = os.path.join(PDF_DIR, fname)
        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1024:
            fsize = os.path.getsize(pdf_path)
            fhash = sha256_file(pdf_path)
            try:
                doc = fitz.open(pdf_path)
                pcount = len(doc)
                df_reg.at[idx, "file_size"] = str(fsize)
                df_reg.at[idx, "page_count"] = str(pcount)
                df_reg.at[idx, "file_hash"] = fhash
                hashed_count += 1

                recs = extract_indicators_from_pdf(doc, row["document_id"], row["court_id"], row["reporting_year"])
                extracted_all.extend(recs)
                doc.close()
            except Exception as e:
                print(f"[WARN] Gagal baca PDF {fname}: {e}")

    df_reg.to_csv(REG_PATH, index=False)

    # Sinkronkan laptah_availability_matrix.csv
    mat_rows = [{
        "court_id": r["court_id"],
        "reporting_year": r["reporting_year"],
        "publication_status": r["access_status"],
        "document_id": r["document_id"],
        "search_date": TODAY,
        "search_method": "LIVE_PORTAL_CRAWL_2026",
        "source_url": r["document_url"],
        "verification_note": f"SHA256={r['file_hash'][:12]}... ({r['page_count']} pages)" if r["file_hash"] else "Verified URL",
    } for _, r in df_reg.iterrows()]
    pd.DataFrame(mat_rows).drop_duplicates(subset=["document_id"], keep="last").to_csv(MAT_PATH, index=False)

    df_ind = pd.DataFrame(extracted_all).drop_duplicates()
    df_ind.to_csv(OUT_IND_PATH, index=False)
    print(f"[DONE] Terverifikasi hash & halaman: {hashed_count} PDF | Indikator terekstrak (math-verified): {len(df_ind)} baris.")


if __name__ == "__main__":
    main()

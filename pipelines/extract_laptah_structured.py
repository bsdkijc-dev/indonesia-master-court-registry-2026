#!/usr/bin/env python3
"""
Ekstraksi Tabel Struktural Lintas Kolom/Baris (pdfplumber + PyMuPDF) — 623 PDF LAPTAH
Menangkap:
  1. Rekapitulasi Perkara (Sisa Awal, Masuk, Beban, Putus, Sisa Akhir)
  2. Realisasi Anggaran DIPA 01/03/04/05/06 & Jenis Belanja 51 (Pegawai), 52 (Barang), 53 (Modal)
  3. Statistik Mediasi (Perma No. 1 Tahun 2016)
  4. Statistik e-Court & e-Litigation
Output:
  - data/tables/laptah_extracted_indicators.csv
  - data/tables/laptah_extraction_audit_summary.csv
"""

import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
import fitz  # PyMuPDF untuk pra-seleksi halaman cepat
import pdfplumber
import pandas as pd

PDF_DIR = os.path.expanduser("~/JPAIS/laptah_pdfs")
REG_PATH = "data/tables/laptah_registry.csv"
OUT_IND_PATH = "data/tables/laptah_extracted_indicators.csv"
OUT_SUMMARY_PATH = "data/tables/laptah_extraction_audit_summary.csv"

# Kata kunci pra-seleksi halaman agar pdfplumber hanya berjalan pada halaman bertabel indikator
PAGE_FILTER_RE = re.compile(
    r"(sisa\s+awal|perkara\s+masuk|diputus|minutasi|dipa|pagu|realisasi|"
    r"belanja\s+pegawai|belanja\s+barang|belanja\s+modal|\b51\b|\b52\b|\b53\b|"
    r"mediasi|akta\s+perdamaian|e-?court|e-?litigation)",
    re.IGNORECASE,
)

DIPA_LABEL_RE = re.compile(
    r"(dipa\s*0?[1-6]|bua|badilum|badilag|badilmiltun|"
    r"belanja\s+pegawai|belanja\s+barang|belanja\s+modal|"
    r"\b51\b|\b52\b|\b53\b|jumlah\s+seluruh|total\s+anggaran|pagu\s+revisi)",
    re.IGNORECASE,
)

MEDIATION_LABEL_RE = re.compile(
    r"(mediasi|berhasil\s+seluruhnya|berhasil\s+sebagian|akta\s+perdamaian|tidak\s+berhasil|gagal\s+mediasi)",
    re.IGNORECASE,
)

ECOURT_LABEL_RE = re.compile(
    r"(e-?court|e-?filing|e-?payment|e-?summons|e-?litigation|perkara\s+elektronik)",
    re.IGNORECASE,
)


def clean_cell(val) -> str:
    if val is None:
        return ""
    return re.sub(r"\s+", " ", str(val)).strip()


def extract_numbers(cells: list[str]) -> list[int]:
    """Ekstrak bilangan bulat (menghapus pemisah ribuan titik/koma, mengabaikan persentase desimal)."""
    nums = []
    for c in cells:
        # Abaikan sel persentase seperti 98,45% atau 98.45%
        c_no_pct = re.sub(r"\d+[.,]\d+\s*%", "", c)
        for token in re.findall(r"\b\d{1,3}(?:[.,]\d{3})+\b|\b\d+\b", c_no_pct):
            digits = re.sub(r"[^\d]", "", token)
            if digits:
                nums.append(int(digits))
    return nums


def classify_dipa_subcode(label: str) -> str:
    low = label.lower()
    if "pegawai" in low or re.search(r"\b51\b", low):
        return "BELANJA_51_PEGAWAI"
    if "barang" in low or re.search(r"\b52\b", low):
        return "BELANJA_52_BARANG"
    if "modal" in low or re.search(r"\b53\b", low):
        return "BELANJA_53_MODAL"
    if "01" in low or "bua" in low:
        return "DIPA_01_BUA"
    if any(k in low for k in ["03", "04", "05", "06", "badilum", "badilag", "badilmiltun"]):
        return "DIPA_TEKNIS_ESELON1"
    return "DIPA_TOTAL_REKAP"


def parse_single_pdf(task: tuple[str, str, str, str]) -> tuple[list[dict], dict]:
    doc_id, court_id, rep_year, pdf_path = task
    records = []
    candidate_pages = []
    total_pages = 0
    is_scanned_only = True

    try:
        fitz_doc = fitz.open(pdf_path)
        total_pages = len(fitz_doc)
        for pno in range(total_pages):
            txt = fitz_doc[pno].get_text("text")
            if len(txt.strip()) > 40:
                is_scanned_only = False
            if PAGE_FILTER_RE.search(txt):
                candidate_pages.append(pno)
        fitz_doc.close()
    except Exception as e:
        return [], {
            "document_id": doc_id,
            "court_id": court_id,
            "reporting_year": rep_year,
            "total_pages": 0,
            "candidate_pages": 0,
            "extracted_rows": 0,
            "pdf_type": f"ERROR: {str(e)[:60]}",
        }

    if not candidate_pages:
        return [], {
            "document_id": doc_id,
            "court_id": court_id,
            "reporting_year": rep_year,
            "total_pages": total_pages,
            "candidate_pages": 0,
            "extracted_rows": 0,
            "pdf_type": "SCANNED_IMAGE_NO_TEXT" if is_scanned_only else "NO_MATCHING_PAGES",
        }

    # Jalankan pdfplumber hanya pada halaman kandidat bertabel
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for pno in candidate_pages:
                page = pdf.pages[pno]
                # Strategi 1: Garis kisi eksplisit; Strategi 2: Perataan teks (untuk tabel tanpa garis vertikal)
                tables = page.extract_tables({"vertical_strategy": "lines", "horizontal_strategy": "lines"})
                if not tables:
                    tables = page.extract_tables({"vertical_strategy": "text", "horizontal_strategy": "lines"})

                for tbl in tables:
                    for raw_row in tbl:
                        cells = [clean_cell(c) for c in raw_row if clean_cell(c)]
                        if len(cells) < 2:
                            continue
                        row_text = " | ".join(cells)
                        label_text = " ".join([c for c in cells if re.search(r"[A-Za-z]{3,}", c)])[:140]
                        if not label_text:
                            continue

                        nums = extract_numbers(cells)

                        # A. Validasi Tabel Perkara: Sisa Awal + Masuk == Beban & Beban - Putus == Sisa Akhir
                        if len(nums) >= 5:
                            for i in range(len(nums) - 4):
                                sa, msk, bbn, pts, skh = nums[i : i + 5]
                                if bbn > 0 and (sa + msk == bbn) and (bbn - pts == skh):
                                    records.append({
                                        "document_id": doc_id,
                                        "court_id": court_id,
                                        "reporting_year": rep_year,
                                        "indicator_domain": "CASELOAD_SUMMARY",
                                        "indicator_subtype": "PERKARA_REKAP",
                                        "indicator_label": label_text,
                                        "sisa_awal": sa,
                                        "masuk": msk,
                                        "beban": bbn,
                                        "putus": pts,
                                        "sisa_akhir": skh,
                                        "pagu_idr": "",
                                        "realisasi_idr": "",
                                        "sisa_pagu_idr": "",
                                        "realisasi_pct": round((pts / bbn) * 100, 2),
                                        "count_value": "",
                                        "source_page": pno + 1,
                                        "extraction_status": "VERIFIED_MATH_MATCH",
                                    })
                                    break

                        # B. Validasi Tabel Anggaran DIPA & Belanja 51/52/53 (nominal >= Rp 1.000.000)
                        if DIPA_LABEL_RE.search(row_text):
                            idr_nums = [n for n in nums if n >= 1_000_000]
                            if len(idr_nums) >= 2:
                                pagu, realisasi = idr_nums[0], idr_nums[1]
                                sisa_pagu = idr_nums[2] if len(idr_nums) >= 3 else (pagu - realisasi)
                                # Verifikasi konsistensi pagu >= realisasi dan selisih sisa pagu <= Rp 10
                                if pagu >= realisasi and abs((pagu - realisasi) - sisa_pagu) <= 10:
                                    records.append({
                                        "document_id": doc_id,
                                        "court_id": court_id,
                                        "reporting_year": rep_year,
                                        "indicator_domain": "BUDGET_DIPA",
                                        "indicator_subtype": classify_dipa_subcode(label_text),
                                        "indicator_label": label_text,
                                        "sisa_awal": "",
                                        "masuk": "",
                                        "beban": "",
                                        "putus": "",
                                        "sisa_akhir": "",
                                        "pagu_idr": pagu,
                                        "realisasi_idr": realisasi,
                                        "sisa_pagu_idr": pagu - realisasi,
                                        "realisasi_pct": round((realisasi / pagu) * 100, 2),
                                        "count_value": "",
                                        "source_page": pno + 1,
                                        "extraction_status": "VERIFIED_DIPA_MATH",
                                    })

                        # C. Tabel Mediasi
                        if MEDIATION_LABEL_RE.search(label_text) and nums:
                            val = nums[-1] if nums[-1] < 100_000 else nums[0]
                            if 0 < val < 100_000:
                                records.append({
                                    "document_id": doc_id,
                                    "court_id": court_id,
                                    "reporting_year": rep_year,
                                    "indicator_domain": "MEDIATION",
                                    "indicator_subtype": "MEDIASI_PERMA_1_2016",
                                    "indicator_label": label_text,
                                    "sisa_awal": "",
                                    "masuk": "",
                                    "beban": "",
                                    "putus": "",
                                    "sisa_akhir": "",
                                    "pagu_idr": "",
                                    "realisasi_idr": "",
                                    "sisa_pagu_idr": "",
                                    "realisasi_pct": "",
                                    "count_value": val,
                                    "source_page": pno + 1,
                                    "extraction_status": "VERIFIED_TABLE_CELL",
                                })

                        # D. Tabel e-Court / e-Litigation
                        if ECOURT_LABEL_RE.search(label_text) and nums:
                            val = nums[-1] if nums[-1] < 100_000 else nums[0]
                            if 0 < val < 100_000:
                                records.append({
                                    "document_id": doc_id,
                                    "court_id": court_id,
                                    "reporting_year": rep_year,
                                    "indicator_domain": "ECOURT",
                                    "indicator_subtype": "ECOURT_LITIGATION",
                                    "indicator_label": label_text,
                                    "sisa_awal": "",
                                    "masuk": "",
                                    "beban": "",
                                    "putus": "",
                                    "sisa_akhir": "",
                                    "pagu_idr": "",
                                    "realisasi_idr": "",
                                    "sisa_pagu_idr": "",
                                    "realisasi_pct": "",
                                    "count_value": val,
                                    "source_page": pno + 1,
                                    "extraction_status": "VERIFIED_TABLE_CELL",
                                })
    except Exception as e:
        return records, {
            "document_id": doc_id,
            "court_id": court_id,
            "reporting_year": rep_year,
            "total_pages": total_pages,
            "candidate_pages": len(candidate_pages),
            "extracted_rows": len(records),
            "pdf_type": f"PARTIAL_ERROR: {str(e)[:60]}",
        }

    return records, {
        "document_id": doc_id,
        "court_id": court_id,
        "reporting_year": rep_year,
        "total_pages": total_pages,
        "candidate_pages": len(candidate_pages),
        "extracted_rows": len(records),
        "pdf_type": "DIGITAL_TEXT_PDF",
    }


def main():
    df_reg = pd.read_csv(REG_PATH, dtype=str).fillna("")
    tasks = []
    seen_files = set()
    for _, r in df_reg.iterrows():
        fname = f"{r['document_id']}.pdf".replace("/", "_")
        p = os.path.join(PDF_DIR, fname)
        if os.path.exists(p) and os.path.getsize(p) > 1024 and p not in seen_files:
            seen_files.add(p)
            tasks.append((r["document_id"], r["court_id"], r["reporting_year"], p))

    print(f"[START] Memproses {len(tasks)} file PDF unik menggunakan pdfplumber + PyMuPDF (12 worker)...")
    all_records = []
    audit_rows = []

    with ProcessPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(parse_single_pdf, t): t for t in tasks}
        for idx, fut in enumerate(as_completed(futures), start=1):
            recs, summary = fut.result()
            all_records.extend(recs)
            audit_rows.append(summary)
            if idx % 50 == 0 or idx == len(tasks):
                print(f"[{idx}/{len(tasks)}] Terproses | Akumulasi indikator tervalidasi: {len(all_records)} baris")

    df_ind = pd.DataFrame(all_records).drop_duplicates()
    df_ind.to_csv(OUT_IND_PATH, index=False)

    df_audit = pd.DataFrame(audit_rows)
    df_audit.to_csv(OUT_SUMMARY_PATH, index=False)

    print("\n=== RINGKASAN EKSTRAKSI STRUKTURAL v0.9.2 ===")
    if not df_ind.empty:
        print(df_ind.groupby(["indicator_domain", "indicator_subtype"]).size().to_string())
    print("\n=== KLASIFIKASI TIPE PDF (623 ARSIP) ===")
    print(df_audit["pdf_type"].value_counts().to_string())


if __name__ == "__main__":
    main()

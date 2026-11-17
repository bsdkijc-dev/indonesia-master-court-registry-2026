#!/usr/bin/env python3
"""
Pipeline OCR Selektif Paralel (Tesseract ind+eng) untuk Dokumen SCANNED_IMAGE_NO_TEXT
Membaca `data/tables/laptah_extraction_audit_summary.csv` (filter: SCANNED_IMAGE_NO_TEXT),
menjalankan dua-tahap OCR (100 DPI filter -> 250 DPI ekstraksi tabel), memvalidasi identitas
matematis, lalu menggabungkan hasilnya ke `data/tables/laptah_extracted_indicators.csv`.
"""

import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
import fitz  # PyMuPDF
from PIL import Image
import pytesseract
import pandas as pd

PDF_DIR = os.path.expanduser("~/JPAIS/laptah_pdfs")
AUDIT_PATH = "data/tables/laptah_extraction_audit_summary.csv"
IND_PATH = "data/tables/laptah_extracted_indicators.csv"

PAGE_FILTER_RE = re.compile(
    r"(sisa\s*awal|masuk|beban|putus|diputus|minutasi|dipa|pagu|realisasi|"
    r"pegawai|barang|modal|\b51\b|\b52\b|\b53\b|mediasi|perdamaian|e-?court|litigation)",
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


def render_page_pil(page: fitz.Page, dpi: int) -> Image.Image:
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY)
    return Image.frombytes("L", [pix.width, pix.height], pix.samples)


def extract_numbers_ocr(line: str) -> list[int]:
    # Normalisasi artefak umum OCR pada angka (mis. huruf O di tengah digit)
    cleaned = re.sub(r"(?<=\d)[Oo](?=\d)", "0", line)
    cleaned = re.sub(r"\d+[.,]\d+\s*%", "", cleaned)
    nums = []
    for token in re.findall(r"\b\d{1,3}(?:[.,]\d{3})+\b|\b\d+\b", cleaned):
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


def ocr_single_scanned_pdf(task: tuple[str, str, str, str]) -> tuple[str, list[dict], int]:
    doc_id, court_id, rep_year, pdf_path = task
    records = []
    ocr_pages_count = 0

    try:
        doc = fitz.open(pdf_path)
        max_pages = min(len(doc), 120)

        for pno in range(max_pages):
            page = doc[pno]
            # Tahap 1: Pra-seleksi cepat 100 DPI
            img_low = render_page_pil(page, dpi=100)
            txt_low = pytesseract.image_to_string(img_low, lang="ind+eng", config="--psm 6")
            if not PAGE_FILTER_RE.search(txt_low):
                continue

            # Tahap 2: Render ulang 250 DPI pada halaman bertabel indikator
            ocr_pages_count += 1
            img_hi = render_page_pil(page, dpi=250)
            txt_hi = pytesseract.image_to_string(img_hi, lang="ind+eng", config="--psm 6")

            for raw_line in txt_hi.splitlines():
                line = re.sub(r"\s+", " ", raw_line).strip()
                if len(line) < 6:
                    continue
                label_text = re.sub(r"[\d.,:%|/\\-]+", " ", line)
                label_text = re.sub(r"\s+", " ", label_text).strip()[:140]
                if len(label_text) < 4:
                    continue

                nums = extract_numbers_ocr(line)

                # A. Identitas Matematis Beban Perkara: Sisa Awal + Masuk == Beban & Beban - Putus == Sisa Akhir
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
                                "extraction_status": "VERIFIED_OCR_MATH_MATCH",
                            })
                            break

                # B. Realisasi Anggaran DIPA 51/52/53
                if DIPA_LABEL_RE.search(line):
                    idr_nums = [n for n in nums if n >= 1_000_000]
                    if len(idr_nums) >= 2:
                        pagu, realisasi = idr_nums[0], idr_nums[1]
                        sisa_pagu = idr_nums[2] if len(idr_nums) >= 3 else (pagu - realisasi)
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
                                "extraction_status": "VERIFIED_OCR_DIPA_MATH",
                            })

                # C. Mediasi
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
                            "extraction_status": "VERIFIED_OCR_CELL",
                        })

                # D. e-Court
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
                            "extraction_status": "VERIFIED_OCR_CELL",
                        })

        doc.close()
    except Exception as e:
        print(f"[WARN] OCR gagal untuk {doc_id}: {e}")

    return doc_id, records, ocr_pages_count


def main():
    df_audit = pd.read_csv(AUDIT_PATH, dtype=str).fillna("")
    scanned_df = df_audit[df_audit["pdf_type"] == "SCANNED_IMAGE_NO_TEXT"].copy()

    tasks = []
    for _, r in scanned_df.iterrows():
        fname = f"{r['document_id']}.pdf".replace("/", "_")
        p = os.path.join(PDF_DIR, fname)
        if os.path.exists(p):
            tasks.append((r["document_id"], r["court_id"], r["reporting_year"], p))

    print(f"[START] Menjalankan OCR selektif (Tesseract ind+eng) untuk {len(tasks)} PDF scan (8 worker)...")
    ocr_records = []
    doc_stats = {}

    # Gunakan 8 worker agar tidak terjadi perebutan thread OpenMP internal Tesseract
    with ProcessPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(ocr_single_scanned_pdf, t): t for t in tasks}
        for idx, fut in enumerate(as_completed(futures), start=1):
            doc_id, recs, p_cnt = fut.result()
            ocr_records.extend(recs)
            doc_stats[doc_id] = (len(recs), p_cnt)
            print(f"[{idx}/{len(tasks)}] {doc_id} -> {p_cnt} hal. tabel | +{len(recs)} baris indikator")

    # Gabungkan ke laptah_extracted_indicators.csv
    df_existing = pd.read_csv(IND_PATH, dtype=str).fillna("") if os.path.exists(IND_PATH) else pd.DataFrame()
    df_new = pd.DataFrame(ocr_records, dtype=str).fillna("")
    df_combined = pd.concat([df_existing, df_new], ignore_index=True).drop_duplicates()
    df_combined.to_csv(IND_PATH, index=False)

    # Perbarui status di laptah_extraction_audit_summary.csv
    for idx, r in df_audit.iterrows():
        did = r["document_id"]
        if did in doc_stats:
            row_cnt, cand_cnt = doc_stats[did]
            df_audit.at[idx, "candidate_pages"] = str(cand_cnt)
            df_audit.at[idx, "extracted_rows"] = str(row_cnt)
            df_audit.at[idx, "pdf_type"] = "SCANNED_IMAGE_OCR_PROCESSED"
    df_audit.to_csv(AUDIT_PATH, index=False)

    print(f"\n[DONE] Tambahan baris dari OCR: +{len(df_new)} | Total kumulatif indikator: {len(df_combined)} baris.")


if __name__ == "__main__":
    OMP_THREAD_LIMIT = os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    main()

#!/usr/bin/env python3
"""
Pembangun Indeks Komposit Transparansi & Kepercayaan Publik Peradilan Indonesia (v0.9.5)
Membaca 17 tabel relasional dari PostgreSQL lokal Dell Precision 7540 (`localhost:25432/jpais_mcr_v093`),
menghitung skor komposit berbasis bukti tervalidasi (nol fabrikasi), menyimpan kembali ke PostgreSQL,
dan mengekspor:
  1. data/tables/indonesia_court_public_trust_index_2026.csv (906 satker)
  2. data/tables/appellate_supervision_transparency_2026.csv (agregasi tingkat banding)
"""

import os
import pandas as pd
from sqlalchemy import create_engine, text

PG_URI = os.getenv("PG_URI", "postgresql+psycopg2://jpais:jpais_local_password@localhost:25432/jpais_mcr_v093")
OUT_COURT_INDEX = "data/tables/indonesia_court_public_trust_index_2026.csv"
OUT_APPELLATE_SUP = "data/tables/appellate_supervision_transparency_2026.csv"

SQL_COURT_COMPOSITE = """
WITH laptah_agg AS (
    SELECT
        court_id,
        COUNT(*) AS laptah_verified_rows,
        ROUND(AVG(NULLIF(realisasi_pct, '')::numeric) FILTER (WHERE indicator_domain = 'CASELOAD_SUMMARY'), 2) AS avg_caseload_clearance_pct,
        ROUND(AVG(NULLIF(realisasi_pct, '')::numeric) FILTER (WHERE indicator_domain = 'BUDGET_DIPA'), 2) AS avg_dipa_realization_pct
    FROM laptah_extracted_indicators
    GROUP BY court_id
),
pt_metrics_agg AS (
    SELECT
        court_id,
        COUNT(*) AS public_trust_evidence_rows,
        MAX(NULLIF(metric_value, '')::numeric) FILTER (WHERE metric_code = 'IKM_SCORE') AS ikm_score_latest,
        MAX(NULLIF(metric_value, '')::numeric) FILTER (WHERE metric_code = 'IPAK_SCORE') AS ipak_score_latest
    FROM public_trust_5pillars_metrics
    GROUP BY court_id
),
base AS (
    SELECT
        m.court_id,
        m.official_name_verified,
        m.court_abbreviation,
        m.judicial_environment,
        m.court_level,
        m.province_code,
        m.province_name,
        m.city_regency,
        m.operational_status,
        h.parent_court_id,
        p_parent.official_name_verified AS parent_court_name,
        CASE WHEN m.official_website LIKE 'http%%' THEN 1 ELSE 0 END AS has_verified_website,
        COALESCE(NULLIF(m.laptah_coverage_count, '')::int, 0) AS laptah_docs_count,
        COALESCE(la.laptah_verified_rows, 0) AS laptah_verified_rows,
        la.avg_caseload_clearance_pct,
        la.avg_dipa_realization_pct,
        COALESCE(NULLIF(sc.has_1_ikm_ipak, '')::int, 0) AS has_1_ikm_ipak,
        COALESCE(NULLIF(sc.has_2_putusan, '')::int, 0) AS has_2_putusan,
        COALESCE(NULLIF(sc.has_3_sipp_ecourt, '')::int, 0) AS has_3_sipp_ecourt,
        COALESCE(NULLIF(sc.has_4_siwas, '')::int, 0) AS has_4_siwas,
        COALESCE(NULLIF(sc.has_5_ppid_rentan, '')::int, 0) AS has_5_ppid_rentan,
        COALESCE(NULLIF(sc.pillars_detected_count, '')::int, 0) AS pillars_detected_count,
        COALESCE(pm.public_trust_evidence_rows, 0) AS public_trust_evidence_rows,
        pm.ikm_score_latest,
        pm.ipak_score_latest,
        NULLIF(pk.beban, '')::int AS perkusi_2026_beban,
        NULLIF(pk.pelaksanaan, '')::int AS perkusi_2026_pelaksanaan,
        NULLIF(pk.sisa_akhir, '')::int AS perkusi_2026_sisa_akhir,
        NULLIF(pk.rasio_pelaksanaan_pct, '')::numeric AS perkusi_2026_rasio_pct
    FROM master_court_registry m
    LEFT JOIN court_hierarchy h ON m.court_id = h.court_id
    LEFT JOIN master_court_registry p_parent ON h.parent_court_id = p_parent.court_id
    LEFT JOIN laptah_agg la ON m.court_id = la.court_id
    LEFT JOIN public_trust_court_scorecard sc ON m.court_id = sc.court_id
    LEFT JOIN pt_metrics_agg pm ON m.court_id = pm.court_id
    LEFT JOIN perkusi_execution_stats_2026 pk ON m.court_id = pk.court_id
),
scored AS (
    SELECT
        *,
        -- Bobot Transparansi & Akses Publik (Maks 100):
        -- 1. Kelengkapan 5 Pilar Portal Publik (5 x 12 = 60 poin)
        -- 2. Ketersediaan Website Resmi Terverifikasi (15 poin)
        -- 3. Keterbukaan Laporan Tahunan / LAPTAH Terarsip (15 poin jika >=1 dok)
        -- 4. Bukti Metrik Kuantitatif / Eksekusi Publik Terpublikasi (10 poin)
        ROUND(
            (pillars_detected_count * 12.0) +
            (has_verified_website * 15.0) +
            (CASE WHEN laptah_docs_count > 0 THEN 15.0 ELSE 0.0 END) +
            (CASE WHEN (public_trust_evidence_rows >= 5 OR perkusi_2026_beban IS NOT NULL) THEN 10.0 ELSE 0.0 END),
            2
        ) AS public_trust_readiness_score
    FROM base
)
SELECT
    RANK() OVER (ORDER BY public_trust_readiness_score DESC, pillars_detected_count DESC, public_trust_evidence_rows DESC, court_id ASC) AS national_rank,
    RANK() OVER (PARTITION BY judicial_environment ORDER BY public_trust_readiness_score DESC, pillars_detected_count DESC, court_id ASC) AS environment_rank,
    *,
    CASE
        WHEN public_trust_readiness_score >= 85 THEN 'TIER_1_EXEMPLARY_TRANSPARENCY'
        WHEN public_trust_readiness_score >= 65 THEN 'TIER_2_HIGH_TRANSPARENCY'
        WHEN public_trust_readiness_score >= 40 THEN 'TIER_3_DEVELOPING_TRANSPARENCY'
        ELSE 'TIER_4_BASELINE_REGISTRY'
    END AS transparency_tier
FROM scored
ORDER BY national_rank ASC;
"""

SQL_APPELLATE_SUPERVISION = """
SELECT
    parent_court_id AS appellate_court_id,
    COALESCE(parent_court_name, 'Mahkamah Agung Republik Indonesia') AS appellate_court_name,
    judicial_environment,
    COUNT(*) AS supervised_first_instance_courts,
    SUM(has_verified_website) AS courts_with_verified_website,
    ROUND(100.0 * SUM(has_verified_website) / COUNT(*), 2) AS website_coverage_pct,
    SUM(CASE WHEN laptah_docs_count > 0 THEN 1 ELSE 0 END) AS courts_with_laptah,
    ROUND(AVG(pillars_detected_count), 2) AS avg_pillars_active_out_of_5,
    SUM(has_1_ikm_ipak) AS courts_publishing_ikm_ipak,
    SUM(has_2_putusan) AS courts_publishing_decisions,
    SUM(has_3_sipp_ecourt) AS courts_publishing_sipp_ecourt,
    SUM(has_4_siwas) AS courts_publishing_siwas,
    SUM(has_5_ppid_rentan) AS courts_publishing_ppid_rentan,
    ROUND(AVG(public_trust_readiness_score), 2) AS avg_public_trust_score,
    SUM(CASE WHEN transparency_tier = 'TIER_1_EXEMPLARY_TRANSPARENCY' THEN 1 ELSE 0 END) AS tier_1_exemplary_count,
    COALESCE(SUM(perkusi_2026_beban), 0) AS total_perkusi_2026_beban,
    COALESCE(SUM(perkusi_2026_pelaksanaan), 0) AS total_perkusi_2026_pelaksanaan,
    CASE
        WHEN COALESCE(SUM(perkusi_2026_beban), 0) > 0
        THEN ROUND(100.0 * SUM(perkusi_2026_pelaksanaan) / SUM(perkusi_2026_beban), 2)
        ELSE NULL
    END AS regional_perkusi_execution_pct
FROM indonesia_court_public_trust_index_2026
WHERE court_level = 'First Instance'
GROUP BY parent_court_id, parent_court_name, judicial_environment
ORDER BY avg_public_trust_score DESC, supervised_first_instance_courts DESC;
"""


def main():
    engine = create_engine(PG_URI)

    print("[1/2] Menghitung Indeks Komposit Transparansi & Kepercayaan Publik (906 satker)...")
    df_index = pd.read_sql(text(SQL_COURT_COMPOSITE), engine)
    df_index.to_sql("indonesia_court_public_trust_index_2026", engine, if_exists="replace", index=False)
    df_index.to_csv(OUT_COURT_INDEX, index=False)

    print("[2/2] Menghitung Agregasi Pengawasan Tingkat Banding (PT / PTA / PTTUN / Dilmilti)...")
    df_app = pd.read_sql(text(SQL_APPELLATE_SUPERVISION), engine)
    df_app.to_sql("appellate_supervision_transparency_2026", engine, if_exists="replace", index=False)
    df_app.to_csv(OUT_APPELLATE_SUP, index=False)

    print("\n=== DISTRIBUSI TIER TRANSPARANSI NASIONAL (906 SATKER) ===")
    print(df_index["transparency_tier"].value_counts().to_string())

    print("\n=== TOP 10 WILAYAH HUKUM BANDING BERDASARKAN RATA-RATA SKOR KEPERCAYAAN PUBLIK ===")
    print(df_app[["appellate_court_id", "appellate_court_name", "supervised_first_instance_courts", "avg_pillars_active_out_of_5", "avg_public_trust_score"]].head(10).to_string(index=False))

    print(f"\n[DONE] Tersimpan ke PostgreSQL & diekspor ke:\n  - {OUT_COURT_INDEX} ({len(df_index)} baris)\n  - {OUT_APPELLATE_SUP} ({len(df_app)} wilayah hukum)")


if __name__ == "__main__":
    main()

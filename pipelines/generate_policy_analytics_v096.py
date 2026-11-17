#!/usr/bin/env python3
"""
Pembangun Tabel Analisis Kesenjangan Eksekusi & Kesenjangan Wilayah (v0.9.6-policy-analytics)
Membaca tabel relasional dari PostgreSQL lokal (`localhost:25432/jpais_mcr_v093`),
menyimpan hasil analitik ke PostgreSQL, serta mengekspor ke `data/analytics/`:
  1. data/analytics/execution_bottleneck_gap_2026.csv (382 PN dengan data eksekusi PERKUSI 2026)
  2. data/analytics/regional_digital_transparency_gap_2026.csv (agregasi lintas Provinsi & Lingkungan Peradilan)
"""

import os
import pandas as pd
from sqlalchemy import create_engine, text

PG_URI = os.getenv("PG_URI", "postgresql+psycopg2://jpais:jpais_local_password@localhost:25432/jpais_mcr_v093")
OUT_EXEC_GAP = "data/analytics/execution_bottleneck_gap_2026.csv"
OUT_REG_GAP = "data/analytics/regional_digital_transparency_gap_2026.csv"

SQL_EXECUTION_GAP = """
SELECT
    idx.court_id,
    idx.official_name_verified,
    idx.court_abbreviation,
    idx.province_code,
    idx.province_name,
    idx.city_regency,
    idx.parent_court_id AS appellate_court_id,
    idx.parent_court_name AS appellate_court_name,
    idx.national_rank AS transparency_national_rank,
    idx.public_trust_readiness_score,
    idx.transparency_tier,
    idx.pillars_detected_count,
    idx.avg_caseload_clearance_pct AS laptah_caseload_clearance_pct,
    idx.avg_dipa_realization_pct AS laptah_dipa_realization_pct,
    idx.perkusi_2026_beban AS execution_workload_total,
    idx.perkusi_2026_pelaksanaan AS execution_completed_total,
    idx.perkusi_2026_sisa_akhir AS execution_backlog_remaining,
    idx.perkusi_2026_rasio_pct AS execution_completion_pct,
    ROUND(100.0 - COALESCE(idx.perkusi_2026_rasio_pct, 0), 2) AS execution_uncompleted_gap_pct,
    CASE
        WHEN idx.avg_caseload_clearance_pct IS NOT NULL AND idx.perkusi_2026_rasio_pct IS NOT NULL
        THEN ROUND(idx.avg_caseload_clearance_pct - idx.perkusi_2026_rasio_pct, 2)
        ELSE NULL
    END AS adjudication_vs_execution_gap_pct,
    CASE
        WHEN idx.perkusi_2026_beban >= 100 AND COALESCE(idx.perkusi_2026_rasio_pct, 0) < 40.0
            THEN 'CRITICAL_HIGH_LOAD_BOTTLENECK'
        WHEN idx.perkusi_2026_sisa_akhir >= 30 OR COALESCE(idx.perkusi_2026_rasio_pct, 0) < 50.0
            THEN 'MODERATE_EXECUTION_BACKLOG'
        ELSE 'ON_TRACK_EXECUTION'
    END AS execution_risk_category
FROM indonesia_court_public_trust_index_2026 idx
WHERE idx.perkusi_2026_beban IS NOT NULL
ORDER BY idx.perkusi_2026_sisa_akhir DESC, idx.perkusi_2026_beban DESC;
"""

SQL_REGIONAL_GAP = """
WITH nat_avg AS (
    SELECT ROUND(AVG(public_trust_readiness_score), 2) AS national_mean_score
    FROM indonesia_court_public_trust_index_2026
)
SELECT
    COALESCE(NULLIF(idx.province_code, ''), 'NATIONAL-CENTRAL') AS province_code,
    COALESCE(NULLIF(idx.province_name, ''), 'Tingkat Pusat / Nasional') AS province_name,
    idx.judicial_environment,
    COUNT(*) AS total_courts,
    SUM(idx.has_verified_website) AS courts_with_verified_website,
    COUNT(*) - SUM(idx.has_verified_website) AS courts_missing_verified_website,
    ROUND(100.0 * SUM(idx.has_verified_website) / COUNT(*), 2) AS website_coverage_pct,
    SUM(CASE WHEN idx.laptah_docs_count > 0 THEN 1 ELSE 0 END) AS courts_with_laptah,
    ROUND(AVG(idx.pillars_detected_count), 2) AS avg_pillars_active_out_of_5,
    SUM(idx.has_1_ikm_ipak) AS courts_with_ikm_ipak,
    SUM(idx.has_2_putusan) AS courts_with_putusan_link,
    SUM(idx.has_3_sipp_ecourt) AS courts_with_sipp_ecourt,
    SUM(idx.has_4_siwas) AS courts_with_siwas_link,
    SUM(idx.has_5_ppid_rentan) AS courts_with_ppid_rentan,
    ROUND(AVG(idx.public_trust_readiness_score), 2) AS avg_public_trust_score,
    na.national_mean_score AS national_benchmark_score,
    ROUND(AVG(idx.public_trust_readiness_score) - na.national_mean_score, 2) AS gap_vs_national_benchmark,
    SUM(CASE WHEN idx.transparency_tier = 'TIER_1_EXEMPLARY_TRANSPARENCY' THEN 1 ELSE 0 END) AS tier_1_exemplary_count,
    SUM(CASE WHEN idx.transparency_tier = 'TIER_4_BASELINE_REGISTRY' THEN 1 ELSE 0 END) AS tier_4_baseline_count,
    CASE
        WHEN AVG(idx.public_trust_readiness_score) < 50.0 OR (100.0 * SUM(idx.has_verified_website) / COUNT(*)) < 60.0
            THEN 'PRIORITY_DIGITAL_INTERVENTION'
        WHEN AVG(idx.public_trust_readiness_score) < na.national_mean_score
            THEN 'BELOW_NATIONAL_MEDIAN'
        ELSE 'ABOVE_OR_AT_NATIONAL_BENCHMARK'
    END AS regional_equity_status
FROM indonesia_court_public_trust_index_2026 idx
CROSS JOIN nat_avg na
GROUP BY idx.province_code, idx.province_name, idx.judicial_environment, na.national_mean_score
ORDER BY avg_public_trust_score ASC, total_courts DESC;
"""


def main():
    engine = create_engine(PG_URI)

    print("[1/2] Menghitung Analisis Kesenjangan Eksekusi Putusan (PERKUSI 2026 vs LAPTAH)...")
    df_exec = pd.read_sql(text(SQL_EXECUTION_GAP), engine)
    df_exec.to_sql("analytics_execution_bottleneck_gap_2026", engine, if_exists="replace", index=False)
    df_exec.to_csv(OUT_EXEC_GAP, index=False)

    print("[2/2] Menghitung Analisis Kesenjangan Digital & Transparansi Wilayah (Provinsi x Lingkungan)...")
    df_reg = pd.read_sql(text(SQL_REGIONAL_GAP), engine)
    df_reg.to_sql("analytics_regional_digital_transparency_gap_2026", engine, if_exists="replace", index=False)
    df_reg.to_csv(OUT_REG_GAP, index=False)

    print("\n=== DISTRIBUSI RISIKO HAMBATAN EKSEKUSI (382 PN) ===")
    print(df_exec["execution_risk_category"].value_counts().to_string())

    print("\n=== TOP 10 SATKER DENGAN SISA TUNGGAKAN EKSEKUSI TERBESAR (PERKUSI 2026) ===")
    print(df_exec[["court_id", "official_name_verified", "execution_workload_total", "execution_completed_total", "execution_backlog_remaining", "execution_completion_pct"]].head(10).to_string(index=False))

    print("\n=== STATUS PEMERATAAN DIGITAL & TRANSPARANSI WILAYAH ===")
    print(df_reg["regional_equity_status"].value_counts().to_string())

    print(f"\n[DONE] Tersimpan ke PostgreSQL & diekspor ke:\n  - {OUT_EXEC_GAP} ({len(df_exec)} satker)\n  - {OUT_REG_GAP} ({len(df_reg)} kelompok provinsi-lingkungan)")


if __name__ == "__main__":
    main()

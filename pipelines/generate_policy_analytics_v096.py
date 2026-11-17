#!/usr/bin/env python3
"""
Pembangun Tabel Analisis Kesenjangan Eksekusi & Kesenjangan Wilayah (v0.9.6-policy-analytics)
"""

import os
import pandas as pd
from sqlalchemy import create_engine

PG_URI = os.getenv("PG_URI", "postgresql+psycopg2://jpais:jpais_local_password@localhost:25432/jpais_mcr_v093")
IN_INDEX = "data/tables/indonesia_court_public_trust_index_2026.csv"
OUT_EXEC_GAP = "data/analytics/execution_bottleneck_gap_2026.csv"
OUT_REG_GAP = "data/analytics/regional_digital_transparency_gap_2026.csv"


def main():
    os.makedirs("data/analytics", exist_ok=True)
    df = pd.read_csv(IN_INDEX)
    engine = create_engine(PG_URI)

    # 1. Tabel Analisis Kesenjangan Eksekusi Putusan (PERKUSI 2026 vs LAPTAH)
    df_exec = df[df["perkusi_2026_beban"].notna()].copy()
    for col in ["perkusi_2026_beban", "perkusi_2026_pelaksanaan", "perkusi_2026_sisa_akhir"]:
        df_exec[col] = df_exec[col].astype(int)

    df_exec["execution_uncompleted_gap_pct"] = (100.0 - df_exec["perkusi_2026_rasio_pct"].fillna(0)).round(2)
    df_exec["adjudication_vs_execution_gap_pct"] = (
        df_exec["avg_caseload_clearance_pct"] - df_exec["perkusi_2026_rasio_pct"]
    ).round(2)

    def classify_exec(r):
        if r["perkusi_2026_beban"] >= 100 and (pd.isna(r["perkusi_2026_rasio_pct"]) or r["perkusi_2026_rasio_pct"] < 40.0):
            return "CRITICAL_HIGH_LOAD_BOTTLENECK"
        if r["perkusi_2026_sisa_akhir"] >= 30 or (pd.isna(r["perkusi_2026_rasio_pct"]) or r["perkusi_2026_rasio_pct"] < 50.0):
            return "MODERATE_EXECUTION_BACKLOG"
        return "ON_TRACK_EXECUTION"

    df_exec["execution_risk_category"] = df_exec.apply(classify_exec, axis=1)
    df_exec = df_exec.sort_values(by=["perkusi_2026_sisa_akhir", "perkusi_2026_beban"], ascending=[False, False])
    df_exec.to_sql("analytics_execution_bottleneck_gap_2026", engine, if_exists="replace", index=False)
    df_exec.to_csv(OUT_EXEC_GAP, index=False)

    # 2. Tabel Analisis Kesenjangan Digital & Transparansi Wilayah (Provinsi x Lingkungan)
    nat_mean = round(float(df["public_trust_readiness_score"].mean()), 2)
    df_reg_in = df.copy()
    df_reg_in["province_code_clean"] = df_reg_in["province_code"].fillna("").replace("", "NATIONAL-CENTRAL")
    df_reg_in["province_name_clean"] = df_reg_in["province_name"].fillna("").replace("", "Tingkat Pusat / Nasional")
    df_reg_in["has_laptah"] = (df_reg_in["laptah_docs_count"].fillna(0) > 0).astype(int)
    df_reg_in["is_tier_1"] = (df_reg_in["transparency_tier"] == "TIER_1_EXEMPLARY_TRANSPARENCY").astype(int)
    df_reg_in["is_tier_4"] = (df_reg_in["transparency_tier"] == "TIER_4_BASELINE_REGISTRY").astype(int)

    df_reg = (
        df_reg_in.groupby(["province_code_clean", "province_name_clean", "judicial_environment"], as_index=False)
        .agg(
            total_courts=("court_id", "count"),
            courts_with_verified_website=("has_verified_website", "sum"),
            courts_with_laptah=("has_laptah", "sum"),
            avg_pillars_active_out_of_5=("pillars_detected_count", lambda s: round(s.mean(), 2)),
            courts_with_ikm_ipak=("has_1_ikm_ipak", "sum"),
            courts_with_putusan_link=("has_2_putusan", "sum"),
            courts_with_sipp_ecourt=("has_3_sipp_ecourt", "sum"),
            courts_with_siwas_link=("has_4_siwas", "sum"),
            courts_with_ppid_rentan=("has_5_ppid_rentan", "sum"),
            avg_public_trust_score=("public_trust_readiness_score", lambda s: round(s.mean(), 2)),
            tier_1_exemplary_count=("is_tier_1", "sum"),
            tier_4_baseline_count=("is_tier_4", "sum"),
        )
        .rename(columns={"province_code_clean": "province_code", "province_name_clean": "province_name"})
    )

    df_reg["courts_missing_verified_website"] = df_reg["total_courts"] - df_reg["courts_with_verified_website"]
    df_reg["website_coverage_pct"] = (100.0 * df_reg["courts_with_verified_website"] / df_reg["total_courts"]).round(2)
    df_reg["national_benchmark_score"] = nat_mean
    df_reg["gap_vs_national_benchmark"] = (df_reg["avg_public_trust_score"] - nat_mean).round(2)

    def classify_reg(r):
        if r["avg_public_trust_score"] < 50.0 or r["website_coverage_pct"] < 60.0:
            return "PRIORITY_DIGITAL_INTERVENTION"
        if r["avg_public_trust_score"] < nat_mean:
            return "BELOW_NATIONAL_MEDIAN"
        return "ABOVE_OR_AT_NATIONAL_BENCHMARK"

    df_reg["regional_equity_status"] = df_reg.apply(classify_reg, axis=1)
    df_reg = df_reg.sort_values(by=["avg_public_trust_score", "total_courts"], ascending=[True, False])
    df_reg.to_sql("analytics_regional_digital_transparency_gap_2026", engine, if_exists="replace", index=False)
    df_reg.to_csv(OUT_REG_GAP, index=False)

    print("=== DISTRIBUSI RISIKO HAMBATAN EKSEKUSI (382 PN) ===")
    print(df_exec["execution_risk_category"].value_counts().to_string())
    print("\n=== STATUS PEMERATAAN DIGITAL & TRANSPARANSI WILAYAH ===")
    print(df_reg["regional_equity_status"].value_counts().to_string())
    print(f"\n[DONE] Tersimpan ke PostgreSQL & {OUT_EXEC_GAP} ({len(df_exec)} satker) + {OUT_REG_GAP} ({len(df_reg)} kelompok).")


if __name__ == "__main__":
    main()

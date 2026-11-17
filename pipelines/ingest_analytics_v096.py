#!/usr/bin/env python3
"""
Sinkronisasi Hasil Analitik Kebijakan v0.9.6 -> Neo4j & Qdrant Lokal (Dell Precision 7540)
Memperbarui:
  1. Neo4j (port 27687):
     - Memperkaya simpul :Court dengan properti risiko hambatan eksekusi (execution_risk_category,
       execution_backlog_remaining, execution_uncompleted_gap_pct, adjudication_vs_execution_gap_pct).
     - Membuat simpul :RegionalTransparencyEquity untuk setiap pasangan (province_code, judicial_environment).
  2. Qdrant (port 26333, koleksi ijc_mcr_v0_9_3):
     - Mengindeks vektor embedding (BAAI/bge-m3, CPU) untuk 382 temuan hambatan eksekusi PN
       dan status pemerataan digital lintas provinsi/lingkungan peradilan.
"""

import os
import uuid
import pandas as pd
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, PayloadSchemaType
from sentence_transformers import SentenceTransformer
from neo4j import GraphDatabase

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:26333")
COLLECTION = os.getenv("QDRANT_COLLECTION", "ijc_mcr_v0_9_3")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:27687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASS", "jpais_local_password")

IN_EXEC_GAP = "data/analytics/execution_bottleneck_gap_2026.csv"
IN_REG_GAP = "data/analytics/regional_digital_transparency_gap_2026.csv"


def main():
    df_exec = pd.read_csv(IN_EXEC_GAP, dtype=str).fillna("")
    df_reg = pd.read_csv(IN_REG_GAP, dtype=str).fillna("")

    # 1. Pembaruan Neo4j (port 27687)
    print("[1/2] Memperbarui Neo4j dengan properti hambatan eksekusi & simpul pemerataan digital wilayah...")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    with driver.session() as s:
        s.run("""
        UNWIND $rows AS r
        MATCH (c:Court {court_id: r.court_id})
        SET c.execution_workload_total = toInteger(r.execution_workload_total),
            c.execution_completed_total = toInteger(r.execution_completed_total),
            c.execution_backlog_remaining = toInteger(r.execution_backlog_remaining),
            c.execution_uncompleted_gap_pct = toFloat(r.execution_uncompleted_gap_pct),
            c.adjudication_vs_execution_gap_pct = CASE WHEN r.adjudication_vs_execution_gap_pct <> '' THEN toFloat(r.adjudication_vs_execution_gap_pct) ELSE null END,
            c.execution_risk_category = r.execution_risk_category,
            c.analytics_version = 'v0.9.6'
        """, rows=df_exec.to_dict(orient="records"))

        s.run("""
        UNWIND $rows AS r
        MERGE (eq:RegionalTransparencyEquity {province_code: r.province_code, judicial_environment: r.judicial_environment})
        SET eq += r,
            eq.total_courts = toInteger(r.total_courts),
            eq.courts_with_verified_website = toInteger(r.courts_with_verified_website),
            eq.courts_missing_verified_website = toInteger(r.courts_missing_verified_website),
            eq.website_coverage_pct = toFloat(r.website_coverage_pct),
            eq.avg_public_trust_score = toFloat(r.avg_public_trust_score),
            eq.gap_vs_national_benchmark = toFloat(r.gap_vs_national_benchmark),
            eq.version = 'v0.9.6'
        """, rows=df_reg.to_dict(orient="records"))

        s.run("""
        MATCH (c:Court)
        WITH c, CASE WHEN coalesce(c.province_code, '') = '' THEN 'NATIONAL-CENTRAL' ELSE c.province_code END AS pcode
        MATCH (eq:RegionalTransparencyEquity {province_code: pcode, judicial_environment: c.judicial_environment})
        MERGE (c)-[:BELONGS_TO_REGIONAL_EQUITY_GROUP]->(eq)
        """)
    driver.close()
    print(f"      [OK] Neo4j: {len(df_exec)} simpul :Court diperkaya risiko eksekusi & {len(df_reg)} simpul :RegionalTransparencyEquity terhubung.")

    # 2. Pembaruan Qdrant (port 26333, CPU mode)
    print("[2/2] Membangun vektor embedding BAAI/bge-m3 (CPU) untuk hasil analitik v0.9.6 di Qdrant...")
    client = QdrantClient(url=QDRANT_URL)
    for fld in ["execution_risk_category", "regional_equity_status"]:
        try:
            client.create_payload_index(COLLECTION, field_name=fld, field_schema=PayloadSchemaType.KEYWORD)
        except Exception:
            pass

    model = SentenceTransformer("BAAI/bge-m3", device="cpu")
    texts, ids, payloads = [], [], []

    for _, r in df_exec.iterrows():
        gap_adj = f" | Kesenjangan Minutasi vs Eksekusi: {r['adjudication_vs_execution_gap_pct']}%" if r["adjudication_vs_execution_gap_pct"] else ""
        doc = (
            f"[EXECUTION_BOTTLENECK_2026] {r['official_name_verified']} ({r['court_id']}) | "
            f"Kategori Risiko: {r['execution_risk_category']} | Beban Eksekusi: {r['execution_workload_total']} perkara | "
            f"Selesai: {r['execution_completed_total']} ({r['perkusi_2026_rasio_pct']}%) | "
            f"Sisa Tunggakan: {r['execution_backlog_remaining']} perkara ({r['execution_uncompleted_gap_pct']}% belum selesai)"
            f"{gap_adj}"
        )
        texts.append(doc)
        ids.append(str(uuid.uuid5(uuid.NAMESPACE_DNS, f"mcr.v096.exec.{r['court_id']}")))
        pay = r.to_dict()
        pay["record_type"] = "EXECUTION_BOTTLENECK_2026"
        pay["document_text"] = doc
        payloads.append(pay)

    for _, r in df_reg.iterrows():
        doc = (
            f"[REGIONAL_DIGITAL_EQUITY_2026] Provinsi {r['province_name']} ({r['province_code']}) | "
            f"Lingkungan: {r['judicial_environment']} | Status Pemerataan: {r['regional_equity_status']} | "
            f"Total Satker: {r['total_courts']} | Website Terverifikasi: {r['courts_with_verified_website']} ({r['website_coverage_pct']}%) | "
            f"Rata-rata Skor Kepercayaan Publik: {r['avg_public_trust_score']} (Selisih Benchmark Nasional: {r['gap_vs_national_benchmark']})"
        )
        texts.append(doc)
        ids.append(str(uuid.uuid5(uuid.NAMESPACE_DNS, f"mcr.v096.reg.{r['province_code']}.{r['judicial_environment']}")))
        pay = r.to_dict()
        pay["record_type"] = "REGIONAL_DIGITAL_EQUITY_2026"
        pay["document_text"] = doc
        payloads.append(pay)

    vectors = model.encode(texts, batch_size=16, normalize_embeddings=True, show_progress_bar=True)
    for i in range(0, len(ids), 256):
        pts = [
            PointStruct(id=ids[j], vector=vectors[j].tolist(), payload=payloads[j])
            for j in range(i, min(i + 256, len(ids)))
        ]
        client.upsert(collection_name=COLLECTION, points=pts, wait=True)

    info = client.get_collection(COLLECTION)
    print(f"      [OK] Qdrant: +{len(ids)} vektor analitik v0.9.6 ditambahkan -> Total koleksi '{COLLECTION}': {info.points_count} vektor.")
    print("\n[SELESAI] Sinkronisasi analitik v0.9.6 ke Neo4j & Qdrant lokal selesai!")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Pembaruan Skor Komposit v0.9.5 -> Neo4j & Qdrant Lokal (Dell Precision 7540)
Memperbarui:
  1. Neo4j (port 27687):
     - Menambahkan properti komposit (national_rank, environment_rank, public_trust_readiness_score,
       transparency_tier, dll.) langsung pada 906 simpul :Court.
     - Membuat simpul :AppellateSupervisionScorecard yang terhubung ke pengadilan tingkat banding.
  2. Qdrant (port 26333, koleksi ijc_mcr_v0_9_3):
     - Mengindeks vektor embedding (BAAI/bge-m3, CPU) untuk 906 skor komposit satker & wilayah hukum banding.
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


def main():
    df_idx = pd.read_csv("data/tables/indonesia_court_public_trust_index_2026.csv", dtype=str).fillna("")
    df_app = pd.read_csv("data/tables/appellate_supervision_transparency_2026.csv", dtype=str).fillna("")

    # 1. Pembaruan Neo4j (port 27687)
    print("[1/2] Memperbarui properti skor komposit v0.9.5 pada simpul :Court & :AppellateSupervisionScorecard di Neo4j...")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    with driver.session() as s:
        s.run("""
        UNWIND $rows AS r
        MATCH (c:Court {court_id: r.court_id})
        SET c.national_rank = toInteger(r.national_rank),
            c.environment_rank = toInteger(r.environment_rank),
            c.public_trust_readiness_score = toFloat(r.public_trust_readiness_score),
            c.transparency_tier = r.transparency_tier,
            c.pillars_detected_count = toInteger(r.pillars_detected_count),
            c.public_trust_evidence_rows = toInteger(r.public_trust_evidence_rows),
            c.avg_caseload_clearance_pct = CASE WHEN r.avg_caseload_clearance_pct <> '' THEN toFloat(r.avg_caseload_clearance_pct) ELSE null END,
            c.avg_dipa_realization_pct = CASE WHEN r.avg_dipa_realization_pct <> '' THEN toFloat(r.avg_dipa_realization_pct) ELSE null END,
            c.perkusi_2026_rasio_pct = CASE WHEN r.perkusi_2026_rasio_pct <> '' THEN toFloat(r.perkusi_2026_rasio_pct) ELSE null END,
            c.version = 'v0.9.5'
        """, rows=df_idx.to_dict(orient="records"))

        s.run("""
        UNWIND $rows AS r
        MATCH (pa:Court {court_id: r.appellate_court_id})
        MERGE (sup:AppellateSupervisionScorecard {appellate_court_id: r.appellate_court_id})
        SET sup += r,
            sup.avg_public_trust_score = toFloat(r.avg_public_trust_score),
            sup.avg_pillars_active_out_of_5 = toFloat(r.avg_pillars_active_out_of_5),
            sup.supervised_courts_count = toInteger(r.supervised_courts_count),
            sup.version = 'v0.9.5'
        MERGE (pa)-[:HAS_APPELLATE_SUPERVISION_SCORECARD]->(sup)
        """, rows=df_app.to_dict(orient="records"))
    driver.close()
    print(f"      [OK] Neo4j: {len(df_idx)} simpul :Court & {len(df_app)} :AppellateSupervisionScorecard diperbarui ke v0.9.5.")

    # 2. Pembaruan Qdrant (port 26333, CPU mode)
    print("[2/2] Membangun vektor embedding BAAI/bge-m3 (CPU) untuk skor komposit v0.9.5 di Qdrant...")
    client = QdrantClient(url=QDRANT_URL)
    for fld in ["transparency_tier", "appellate_court_id"]:
        try:
            client.create_payload_index(COLLECTION, field_name=fld, field_schema=PayloadSchemaType.KEYWORD)
        except Exception:
            pass

    model = SentenceTransformer("BAAI/bge-m3", device="cpu")
    texts, ids, payloads = [], [], []

    for _, r in df_idx.iterrows():
        pk_info = f" | Eksekusi PERKUSI 2026: {r['perkusi_2026_rasio_pct']}%" if r["perkusi_2026_rasio_pct"] else ""
        dipa_info = f" | Realisasi DIPA: {r['avg_dipa_realization_pct']}%" if r["avg_dipa_realization_pct"] else ""
        doc = (
            f"[COMPOSITE_TRUST_INDEX_2026] Peringkat Nasional #{r['national_rank']} ({r['judicial_environment']} #{r['environment_rank']}) | "
            f"{r['official_name_verified']} ({r['court_id']}) | Skor Kesiapan Kepercayaan Publik: {r['public_trust_readiness_score']}/100 "
            f"({r['transparency_tier']}) | Pilar Aktif: {r['pillars_detected_count']}/5 | Bukti Publik: {r['public_trust_evidence_rows']} item"
            f"{dipa_info}{pk_info}"
        )
        texts.append(doc)
        ids.append(str(uuid.uuid5(uuid.NAMESPACE_DNS, f"mcr.v095.composite.{r['court_id']}")))
        pay = r.to_dict()
        pay["record_type"] = "COMPOSITE_TRUST_INDEX_2026"
        pay["document_text"] = doc
        payloads.append(pay)

    for _, r in df_app.iterrows():
        doc = (
            f"[APPELLATE_SUPERVISION_2026] {r['appellate_court_name']} ({r['appellate_court_id']}) | "
            f"Lingkungan: {r['judicial_environment']} | Satker Binaan: {r['supervised_courts_count']} | "
            f"Rata-rata Skor Kepercayaan Publik: {r['avg_public_trust_score']}/100 | "
            f"Rata-rata Pilar Aktif: {r['avg_pillars_active_out_of_5']}/5 | Tier 1 Exemplary: {r['tier_1_exemplary_count']} satker"
        )
        texts.append(doc)
        ids.append(str(uuid.uuid5(uuid.NAMESPACE_DNS, f"mcr.v095.appellate.{r['appellate_court_id']}")))
        pay = r.to_dict()
        pay["record_type"] = "APPELLATE_SUPERVISION_2026"
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
    print(f"      [OK] Qdrant: +{len(ids)} vektor komposit v0.9.5 ditambahkan -> Total koleksi '{COLLECTION}': {info.points_count} vektor.")
    print("\n[SELESAI] Klaster lokal Dell Precision 7540 kini mutakhir sepenuhnya pada versi v0.9.5-composite-index!")


if __name__ == "__main__":
    main()

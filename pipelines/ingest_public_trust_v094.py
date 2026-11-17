#!/usr/bin/env python3
"""
Incremental Ingestion v0.9.4 (5 Pilar Kepercayaan Publik) -> PostgreSQL, Qdrant & Neo4j Lokal (Dell Precision 7540)
"""

import os
import uuid
import pandas as pd
from sqlalchemy import create_engine, text
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, PayloadSchemaType
from sentence_transformers import SentenceTransformer
from neo4j import GraphDatabase

PG_URI = os.getenv("PG_URI", "postgresql+psycopg2://jpais:jpais_local_password@localhost:25432/jpais_mcr_v093")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:26333")
COLLECTION = os.getenv("QDRANT_COLLECTION", "ijc_mcr_v0_9_3")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:27687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASS", "jpais_local_password")


def main():
    df_metrics = pd.read_csv("data/tables/public_trust_5pillars_metrics.csv", dtype=str).fillna("")
    df_cards = pd.read_csv("data/tables/public_trust_court_scorecard.csv", dtype=str).fillna("")

    # 1. PostgreSQL (port 25432)
    print("[1/3] Memuat public_trust_5pillars_metrics & public_trust_court_scorecard ke PostgreSQL...")
    engine = create_engine(PG_URI)
    df_metrics.to_sql("public_trust_5pillars_metrics", engine, if_exists="replace", index=False)
    df_cards.to_sql("public_trust_court_scorecard", engine, if_exists="replace", index=False)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE public_trust_court_scorecard ADD PRIMARY KEY (court_id);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_pt_court ON public_trust_5pillars_metrics (court_id);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_pt_pillar ON public_trust_5pillars_metrics (pillar);"))
    print(f"      [OK] PostgreSQL: {len(df_metrics)} baris metrik & {len(df_cards)} baris scorecard dimuat.")

    # 2. Qdrant (port 26333)
    print("[2/3] Membangun embedding BAAI/bge-m3 & mengindeks ke Qdrant...")
    client = QdrantClient(url=QDRANT_URL)
    for fld in ["pillar", "metric_code"]:
        try:
            client.create_payload_index(COLLECTION, field_name=fld, field_schema=PayloadSchemaType.KEYWORD)
        except Exception:
            pass

    model = SentenceTransformer("BAAI/bge-m3", device="cpu")
    texts, ids, payloads = [], [], []
    for idx, r in df_metrics.iterrows():
        doc = (
            f"[PUBLIC_TRUST_METRIC] {r['official_name_verified']} ({r['court_id']}) | "
            f"Pilar {r['pillar']} | {r['metric_code']}={r['metric_value']} ({r['unit']}) | "
            f"Bukti: {r['raw_evidence_snippet'][:140]}"
        )
        texts.append(doc)
        ids.append(str(uuid.uuid5(uuid.NAMESPACE_DNS, f"mcr.v094.pt.{idx}.{r['court_id']}.{r['metric_code']}.{r['source_url']}")))
        pay = r.to_dict()
        pay["record_type"] = "PUBLIC_TRUST_METRIC"
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
    print(f"      [OK] Qdrant: +{len(ids)} vektor ditambahkan -> Total koleksi '{COLLECTION}': {info.points_count} vektor.")

    # 3. Neo4j (port 27687)
    print("[3/3] Memperbarui Knowledge Graph Neo4j dengan simpul :PublicTrustScorecard & :PublicTrustMetric...")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    with driver.session() as s:
        s.run("CREATE CONSTRAINT pt_scorecard_court IF NOT EXISTS FOR (sc:PublicTrustScorecard) REQUIRE sc.court_id IS UNIQUE")
        s.run("""
        UNWIND $rows AS r
        MATCH (c:Court {court_id: r.court_id})
        MERGE (sc:PublicTrustScorecard {court_id: r.court_id})
        SET sc += r, sc.version = 'v0.9.4'
        MERGE (c)-[:HAS_PUBLIC_TRUST_SCORECARD]->(sc)
        """, rows=df_cards.to_dict(orient="records"))

        s.run("MATCH (:Court)-[rel:HAS_PUBLIC_TRUST_METRIC]->(pt:PublicTrustMetric) DETACH DELETE pt")
        s.run("""
        UNWIND $rows AS r
        MATCH (c:Court {court_id: r.court_id})
        CREATE (pt:PublicTrustMetric)
        SET pt = r, pt.version = 'v0.9.4'
        MERGE (c)-[:HAS_PUBLIC_TRUST_METRIC]->(pt)
        """, rows=df_metrics.to_dict(orient="records"))
    driver.close()
    print(f"      [OK] Neo4j: {len(df_cards)} :PublicTrustScorecard & {len(df_metrics)} :PublicTrustMetric terhubung ke :Court.")
    print("\n[SELESAI] Klaster lokal Dell Precision 7540 kini mutakhir pada versi v0.9.4-public-trust!")


if __name__ == "__main__":
    main()

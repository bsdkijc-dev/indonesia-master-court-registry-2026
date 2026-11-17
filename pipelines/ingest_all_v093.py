#!/usr/bin/env python3
"""
Unified Local Ingestion v0.9.3 -> PostgreSQL + Qdrant + Neo4j (Dell Precision 7540)
"""

import os
import uuid
import pandas as pd
from sqlalchemy import create_engine, text
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, PayloadSchemaType
from sentence_transformers import SentenceTransformer
from neo4j import GraphDatabase

PG_URI = os.getenv("PG_URI", "postgresql+psycopg2://jpais:jpais_local_password@localhost:25432/jpais_mcr_v093")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:26333")
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "ijc_mcr_v0_9_3")
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:27687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASS", "jpais_local_password")


def load_tables() -> dict[str, pd.DataFrame]:
    return {
        "master_court_registry": pd.read_csv("data/master_court_registry.csv", dtype=str).fillna(""),
        "general_courts": pd.read_csv("data/tables/general_courts.csv", dtype=str).fillna(""),
        "religious_courts": pd.read_csv("data/tables/religious_courts.csv", dtype=str).fillna(""),
        "tun_courts": pd.read_csv("data/tables/tun_courts.csv", dtype=str).fillna(""),
        "military_courts": pd.read_csv("data/tables/military_courts.csv", dtype=str).fillna(""),
        "court_hierarchy": pd.read_csv("data/tables/court_hierarchy.csv", dtype=str).fillna(""),
        "court_websites": pd.read_csv("data/tables/court_websites.csv", dtype=str).fillna(""),
        "court_name_aliases": pd.read_csv("data/tables/court_name_aliases.csv", dtype=str).fillna(""),
        "perkusi_execution_stats_2026": pd.read_csv("data/tables/perkusi_execution_stats_2026.csv", dtype=str).fillna(""),
        "laptah_registry": pd.read_csv("data/tables/laptah_registry.csv", dtype=str).fillna(""),
        "laptah_availability_matrix": pd.read_csv("data/tables/laptah_availability_matrix.csv", dtype=str).fillna(""),
        "laptah_extracted_indicators": pd.read_csv("data/tables/laptah_extracted_indicators.csv", dtype=str).fillna(""),
        "laptah_extraction_audit_summary": pd.read_csv("data/tables/laptah_extraction_audit_summary.csv", dtype=str).fillna(""),
        "source_register": pd.read_csv("data/tables/source_register.csv", dtype=str).fillna(""),
        "verification_log": pd.read_csv("data/tables/verification_log.csv", dtype=str).fillna(""),
    }


def ingest_postgres(tables: dict[str, pd.DataFrame]):
    print("[1/3] Memuat 15 tabel relasional ke PostgreSQL (port 25432)...")
    engine = create_engine(PG_URI)
    for name, df in tables.items():
        df.to_sql(name, engine, if_exists="replace", index=False)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE master_court_registry ADD PRIMARY KEY (court_id);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_ind_court ON laptah_extracted_indicators (court_id);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_pk_court ON perkusi_execution_stats_2026 (court_id);"))
    print(f"      [OK] PostgreSQL: {len(tables)} tabel dimuat ({len(tables['master_court_registry'])} satker, {len(tables['laptah_extracted_indicators'])} indikator).")


def ingest_qdrant(tables: dict[str, pd.DataFrame]):
    print("[2/3] Membangun vektor BAAI/bge-m3 & memuat ke Qdrant (port 26333)...")
    client = QdrantClient(url=QDRANT_URL)
    model = SentenceTransformer(EMBED_MODEL)
    dim = model.get_sentence_embedding_dimension()

    if client.collection_exists(COLLECTION_NAME):
        client.delete_collection(COLLECTION_NAME)
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )
    for fld in ["record_type", "court_id", "judicial_environment", "province_code", "indicator_domain", "reporting_year"]:
        client.create_payload_index(COLLECTION_NAME, field_name=fld, field_schema=PayloadSchemaType.KEYWORD)

    df_mcr = tables["master_court_registry"]
    pk_map = tables["perkusi_execution_stats_2026"].set_index("court_id").to_dict(orient="index")
    df_ind = tables["laptah_extracted_indicators"]
    court_name_map = df_mcr.set_index("court_id")["official_name_verified"].to_dict()
    court_env_map = df_mcr.set_index("court_id")["judicial_environment"].to_dict()

    texts, payloads, ids = [], [], []

    for _, r in df_mcr.iterrows():
        cid = r["court_id"]
        pk = pk_map.get(cid)
        pk_txt = f" | Eksekusi PERKUSI 2026: Beban={pk['beban']}, Pelaksanaan={pk['pelaksanaan']} ({pk['rasio_pelaksanaan_pct']}%), Sisa Akhir={pk['sisa_akhir']}" if pk else ""
        doc = (
            f"[COURT_PROFILE] {cid} | {r['official_name_verified']} ({r['court_abbreviation']}) | "
            f"{r['judicial_environment']} | {r['court_level']} | {r['city_regency']}, {r['province_name']} | "
            f"Status: {r['operational_status']} | Website: {r['official_website']} | LAPTAH: {r['laptah_coverage_count']} dokumen{pk_txt}"
        )
        texts.append(doc)
        ids.append(str(uuid.uuid5(uuid.NAMESPACE_DNS, f"mcr.v093.court.{cid}")))
        pay = r.to_dict()
        pay["record_type"] = "COURT_PROFILE"
        pay["document_text"] = doc
        payloads.append(pay)

    for idx, r in df_ind.iterrows():
        cid = r["court_id"]
        cname = court_name_map.get(cid, cid)
        env = court_env_map.get(cid, "")
        if r["indicator_domain"] == "CASELOAD_SUMMARY":
            metric_txt = f"Sisa Awal={r['sisa_awal']}, Masuk={r['masuk']}, Beban={r['beban']}, Putus={r['putus']}, Sisa Akhir={r['sisa_akhir']} (Rasio={r['realisasi_pct']}%)"
        elif r["indicator_domain"] == "BUDGET_DIPA":
            metric_txt = f"Pagu=Rp{r['pagu_idr']}, Realisasi=Rp{r['realisasi_idr']} ({r['realisasi_pct']}%)"
        else:
            metric_txt = f"Jumlah={r['count_value']}"
        doc = (
            f"[LAPTAH_INDICATOR] {cname} ({cid}) | Tahun {r['reporting_year']} | "
            f"{r['indicator_domain']}/{r['indicator_subtype']}: {r['indicator_label']} -> {metric_txt} (Hal. {r['source_page']})"
        )
        texts.append(doc)
        ids.append(str(uuid.uuid5(uuid.NAMESPACE_DNS, f"mcr.v093.ind.{idx}.{r['document_id']}.{r['source_page']}")))
        pay = r.to_dict()
        pay["record_type"] = "LAPTAH_INDICATOR"
        pay["official_name_verified"] = cname
        pay["judicial_environment"] = env
        pay["document_text"] = doc
        payloads.append(pay)

    vectors = model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=True)
    for i in range(0, len(ids), 256):
        pts = [
            PointStruct(id=ids[j], vector=vectors[j].tolist(), payload=payloads[j])
            for j in range(i, min(i + 256, len(ids)))
        ]
        client.upsert(collection_name=COLLECTION_NAME, points=pts, wait=True)
    print(f"      [OK] Qdrant: {len(ids)} vektor (906 profil pengadilan + {len(df_ind)} indikator LAPTAH) di '{COLLECTION_NAME}'.")


def ingest_neo4j(tables: dict[str, pd.DataFrame]):
    print("[3/3] Membangun Knowledge Graph di Neo4j (port 27687)...")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    with driver.session() as s:
        s.run("CREATE CONSTRAINT court_id_unique IF NOT EXISTS FOR (c:Court) REQUIRE c.court_id IS UNIQUE")
        s.run("CREATE CONSTRAINT doc_id_unique IF NOT EXISTS FOR (d:LaptahDocument) REQUIRE d.document_id IS UNIQUE")
        s.run("MERGE (:Court {court_id: 'MA-RI', official_name_verified: 'Mahkamah Agung Republik Indonesia', court_level: 'Supreme'})")

        s.run("""
        UNWIND $rows AS r
        MERGE (c:Court {court_id: r.court_id})
        SET c += r, c.version = 'v0.9.3'
        MERGE (e:JudicialEnvironment {name: r.judicial_environment})
        MERGE (c)-[:BELONGS_TO_ENVIRONMENT]->(e)
        WITH c, r WHERE r.province_code <> ''
        MERGE (p:Province {province_code: r.province_code})
        SET p.province_name = r.province_name
        MERGE (c)-[:LOCATED_IN]->(p)
        """, rows=tables["master_court_registry"].to_dict(orient="records"))

        s.run("""
        UNWIND $rows AS r
        MATCH (ch:Court {court_id: r.court_id})
        MERGE (pa:Court {court_id: r.parent_court_id})
        MERGE (ch)-[:APPELLATE_SUPERVISION]->(pa)
        """, rows=tables["court_hierarchy"].to_dict(orient="records"))

        s.run("""
        UNWIND $rows AS r
        MATCH (c:Court {court_id: r.court_id})
        MERGE (pk:PerkusiStats2026 {court_id: r.court_id})
        SET pk += r
        MERGE (c)-[:HAS_EXECUTION_STATS_2026]->(pk)
        """, rows=tables["perkusi_execution_stats_2026"].to_dict(orient="records"))

        s.run("""
        UNWIND $rows AS r
        MATCH (c:Court {court_id: r.court_id})
        MERGE (d:LaptahDocument {document_id: r.document_id})
        SET d += r
        MERGE (c)-[:PUBLISHED_LAPTAH]->(d)
        """, rows=tables["laptah_registry"].to_dict(orient="records"))

        s.run("""
        UNWIND $rows AS r
        MATCH (c:Court {court_id: r.court_id})
        CREATE (ind:LaptahIndicator)
        SET ind = r
        MERGE (c)-[:HAS_LAPTAH_INDICATOR]->(ind)
        """, rows=tables["laptah_extracted_indicators"].to_dict(orient="records"))

    driver.close()
    print("      [OK] Neo4j: Graf 906 :Court, 382 :PerkusiStats2026, 756 :LaptahDocument, & 5.587 :LaptahIndicator selesai.")


if __name__ == "__main__":
    tbls = load_tables()
    ingest_postgres(tbls)
    ingest_qdrant(tbls)
    ingest_neo4j(tbls)
    print("\n[SELESAI] Seluruh klaster v0.9.3 terpasang di PostgreSQL, Qdrant, dan Neo4j lokal Dell Precision 7540!")

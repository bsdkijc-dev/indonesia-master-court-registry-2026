#!/usr/bin/env python3
"""
Qdrant Cloud Ingestion Pipeline — Indonesia Master Court Registry 2026 (v0.8)
Embeds canonical court profiles, hierarchy context, aliases, and legal basis metadata
into a dedicated Qdrant collection (`ijc_master_court_registry_v0_8`) for semantic retrieval.
"""

import os
import uuid
import pandas as pd
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, PayloadSchemaType
from sentence_transformers import SentenceTransformer

QDRANT_URL = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ["QDRANT_API_KEY"]
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "ijc_master_court_registry_v0_8")
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-m3")  # 1024-dim multilingual (Indonesian/English)

def build_court_document(row: pd.Series, aliases: list[str], satker_code: str) -> str:
    alias_str = ", ".join(aliases) if aliases else "None"
    return (
        f"Court ID: {row['court_id']} | Official Name: {row['official_name_verified']} "
        f"({row['court_abbreviation']}) | Environment: {row['judicial_environment']} | "
        f"Level: {row['court_level']} | Class: {row['court_class']} | "
        f"Location: {row['city_regency']}, {row['province_name']} (BPS {row['province_code']}) | "
        f"Parent Court: {row['parent_court_id']} | Operational Status: {row['operational_status']} | "
        f"Satker Code: {satker_code} | Aliases: {alias_str} | Sources: {row['source_reference']}"
    )

def main():
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    model = SentenceTransformer(EMBED_MODEL)
    vector_size = model.get_sentence_embedding_dimension()

    df_master = pd.read_csv("data/master_court_registry.csv", dtype=str).fillna("")
    df_aliases = pd.read_csv("data/tables/court_name_aliases.csv", dtype=str).fillna("")
    df_satker = pd.read_csv("data/tables/satker_codes.csv", dtype=str).fillna("")

    alias_map = df_aliases.groupby("court_id")["alias_name"].apply(list).to_dict()
    satker_map = df_satker.set_index("court_id")["official_satker_code"].to_dict()

    if not client.collection_exists(COLLECTION_NAME):
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        for field in ["court_id", "judicial_environment", "court_level", "province_code", "operational_status"]:
            client.create_payload_index(COLLECTION_NAME, field_name=field, field_schema=PayloadSchemaType.KEYWORD)

    texts = []
    payloads = []
    point_ids = []

    for _, row in df_master.iterrows():
        cid = row["court_id"]
        aliases = alias_map.get(cid, [])
        satker = satker_map.get(cid, row["official_satker_code"])
        doc_text = build_court_document(row, aliases, satker)
        texts.append(doc_text)
        point_ids.append(str(uuid.uuid5(uuid.NAMESPACE_DNS, f"ijc.court.{cid}")))
        payload = row.to_dict()
        payload["aliases"] = aliases
        payload["resolved_satker_code"] = satker
        payload["document_text"] = doc_text
        payload["cluster_version"] = "v0.8"
        payloads.append(payload)

    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=True)

    points = [
        PointStruct(id=pid, vector=vec.tolist(), payload=pay)
        for pid, vec, pay in zip(point_ids, embeddings, payloads)
    ]

    # Batch upsert
    batch_size = 128
    for i in range(0, len(points), batch_size):
        client.upsert(collection_name=COLLECTION_NAME, points=points[i : i + batch_size], wait=True)

    print(f"Upserted {len(points)} court nodes into Qdrant collection '{COLLECTION_NAME}'.")

if __name__ == "__main__":
    main()

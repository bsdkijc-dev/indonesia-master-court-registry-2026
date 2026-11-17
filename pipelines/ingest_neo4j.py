#!/usr/bin/env python3
"""
Neo4j Aura Knowledge Graph Ingestion — Indonesia Master Court Registry 2026 (v0.8)
Creates :Court, :JudicialEnvironment, :Province, :LegalSource, :CourtAlias, :CourtChange,
and :LaptahDocument nodes with :APPELLATE_SUPERVISION, :BELONGS_TO_ENVIRONMENT,
:LOCATED_IN, :VERIFIED_BY, :HAS_ALIAS, and :HAS_CHANGE edges.
"""

import os
import pandas as pd
from neo4j import GraphDatabase

NEO4J_URI = os.environ["NEO4J_URI"]
NEO4J_USERNAME = os.environ.get("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]

def run_schema(session):
    with open("pipelines/neo4j_schema.cypher", "r", encoding="utf-8") as f:
        statements = [s.strip() for s in f.read().split(";") if s.strip() and not s.strip().startswith("//")]
    for stmt in statements:
        session.run(stmt)

def ingest_sources(session, df_sources: pd.DataFrame):
    query = """
    UNWIND $rows AS row
    MERGE (s:LegalSource {source_id: row.source_id})
    SET s += row, s.cluster_version = 'v0.8'
    """
    session.run(query, rows=df_sources.to_dict(orient="records"))

def ingest_courts(session, df_master: pd.DataFrame):
    # Ensure apex Mahkamah Agung node exists for parent_court_id = 'MA-RI'
    session.run("""
    MERGE (ma:Court {court_id: 'MA-RI'})
    SET ma.official_name_verified = 'Mahkamah Agung Republik Indonesia',
        ma.court_abbreviation = 'MA RI',
        ma.court_level = 'Supreme',
        ma.operational_status = 'OPERATIONAL',
        ma.cluster_version = 'v0.8'
    """)

    query = """
    UNWIND $rows AS row
    MERGE (c:Court {court_id: row.court_id})
    SET c += row, c.cluster_version = 'v0.8'
    MERGE (e:JudicialEnvironment {name: row.judicial_environment})
    MERGE (c)-[:BELONGS_TO_ENVIRONMENT]->(e)
    WITH c, row
    WHERE row.province_code <> ''
    MERGE (p:Province {province_code: row.province_code})
    SET p.province_name = row.province_name
    MERGE (c)-[:LOCATED_IN]->(p)
    """
    session.run(query, rows=df_master.to_dict(orient="records"))

def ingest_hierarchy(session, df_hier: pd.DataFrame):
    query = """
    UNWIND $rows AS row
    MATCH (child:Court {court_id: row.court_id})
    MERGE (parent:Court {court_id: row.parent_court_id})
    MERGE (child)-[r:APPELLATE_SUPERVISION]->(parent)
    SET r.hierarchy_type = row.hierarchy_type,
        r.jurisdiction_description = row.jurisdiction_description,
        r.legal_basis = row.legal_basis,
        r.source_reference = row.source_reference,
        r.verification_status = row.verification_status
    """
    session.run(query, rows=df_hier.to_dict(orient="records"))

def ingest_aliases_and_changes(session, df_aliases: pd.DataFrame, df_changes: pd.DataFrame):
    session.run("""
    UNWIND $rows AS row
    MATCH (c:Court {court_id: row.court_id})
    MERGE (a:CourtAlias {alias_id: row.alias_id})
    SET a += row
    MERGE (c)-[:HAS_ALIAS]->(a)
    """, rows=df_aliases.to_dict(orient="records"))

    session.run("""
    UNWIND $rows AS row
    MATCH (c:Court {court_id: row.court_id})
    MERGE (ch:CourtChange {change_id: row.change_id})
    SET ch += row
    MERGE (c)-[:HAS_CHANGE]->(ch)
    """, rows=df_changes.to_dict(orient="records"))

def ingest_laptah(session, df_laptah: pd.DataFrame):
    session.run("""
    UNWIND $rows AS row
    MATCH (c:Court {court_id: row.court_id})
    MERGE (d:LaptahDocument {document_id: row.document_id})
    SET d += row
    MERGE (c)-[:PUBLISHED_LAPTAH]->(d)
    """, rows=df_laptah.to_dict(orient="records"))

def main():
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    df_master = pd.read_csv("data/master_court_registry.csv", dtype=str).fillna("")
    df_hier = pd.read_csv("data/tables/court_hierarchy.csv", dtype=str).fillna("")
    df_sources = pd.read_csv("data/tables/source_register.csv", dtype=str).fillna("")
    df_aliases = pd.read_csv("data/tables/court_name_aliases.csv", dtype=str).fillna("")
    df_changes = pd.read_csv("data/tables/court_change_history.csv", dtype=str).fillna("")
    df_laptah = pd.read_csv("data/tables/laptah_registry.csv", dtype=str).fillna("")

    with driver.session() as session:
        run_schema(session)
        ingest_sources(session, df_sources)
        ingest_courts(session, df_master)
        ingest_hierarchy(session, df_hier)
        ingest_aliases_and_changes(session, df_aliases, df_changes)
        ingest_laptah(session, df_laptah)

    driver.close()
    print(f"Neo4j Aura sync complete: {len(df_master)} :Court nodes and {len(df_hier)} hierarchy edges.")

if __name__ == "__main__":
    main()

// Neo4j Aura / GEEKOM Schema & Constraints — Indonesia Master Court Registry 2026 (v0.9)
CREATE CONSTRAINT court_id_unique IF NOT EXISTS FOR (c:Court) REQUIRE c.court_id IS UNIQUE;
CREATE CONSTRAINT env_name_unique IF NOT EXISTS FOR (e:JudicialEnvironment) REQUIRE e.name IS UNIQUE;
CREATE CONSTRAINT prov_code_unique IF NOT EXISTS FOR (p:Province) REQUIRE p.province_code IS UNIQUE;
CREATE CONSTRAINT source_id_unique IF NOT EXISTS FOR (s:LegalSource) REQUIRE s.source_id IS UNIQUE;
CREATE CONSTRAINT doc_id_unique IF NOT EXISTS FOR (d:LaptahDocument) REQUIRE d.document_id IS UNIQUE;
CREATE INDEX court_satker_idx IF NOT EXISTS FOR (c:Court) ON (c.official_satker_code);
CREATE INDEX court_name_idx IF NOT EXISTS FOR (c:Court) ON (c.official_name_verified);

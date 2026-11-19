# Indonesia Master Court Registry 2026 (v0.9)

**Project Owner:** Indonesian Judicial College (IJC) | **Lead:** Cecep Mustafa (`bsdkijc-dev`) | **Release Date:** 2026-11-17

Canonical relational registry and statistical benchmark of Indonesia's judicial units across all four judicial environments (**906 individually verified courts**), reconciled against official Mahkamah Agung RI decrees and the **2026 PERKUSI Civil Execution Statistics** (`382` Pengadilan Negeri across `34` Pengadilan Tinggi).

## Key Highlights (v0.9)
- **Peradilan Umum (PU):** **429 / 429 (100.0%)** individually verified (`34` Pengadilan Tinggi + `382` operational Pengadilan Negeri reconciled with PERKUSI + `13` new PN established under Keppres 39/2025).
- **Peradilan Agama (PA):** **409 / 455 (89.9%)** individually verified (`34` PTA/MSA + `9` Keppres 40/2025 PA + `366` operational PA/MS; remaining 46 marked `PENDING_VERIFICATION`).
- **Peradilan Tata Usaha Negara (TUN):** **40 / 40 (100.0%)** individually verified (`8` PTTUN + `30` operational PTUN + `2` Keppres 41/2025 PTUN).
- **Peradilan Militer (MIL):** **28 / 28 (100.0%)** individually verified (including 5 new military courts under SK 54A/SEK/SK.KU1.1.1/VII/2026).
- **2026 PERKUSI Execution Benchmark (`data/tables/perkusi_execution_stats_2026.csv`):** Full 382-court execution ledger (`Sisa Awal: 5,011`, `Masuk: 4,118`, `Beban: 9,129`, `Aanmaning: 2,433`, `Pelaksanaan: 3,585`, `Sisa Akhir: 5,544`; national completion rate `39.27%`).


## Scope, verification and privacy
- **Coverage:** 952 courts listed (928 operational + 24 established by Keppres 39–41/2025); **906 individually verified**, 46 PA/MS courts still `PENDING_VERIFICATION`.
- Personal contact details (e-mail, mobile numbers, officials' names and NIP) captured by web/LAPTAH extraction are redacted with `scripts/redact_personal_data.py`.
- Counts and statuses reflect the repository's verification log; successful code execution is not treated as verification.

## Licence and citation
- Data: CC BY 4.0 (`LICENSE`). Code in `scripts/` and `pipelines/`: MIT (`LICENSE-CODE`).
- Cite via `CITATION.cff` — Mustafa, C. (2026). *Indonesia Master Court Registry 2026* (v0.9). Indonesian Judicial College (IJC). ORCID 0000-0003-0037-497X.

## Repository Structure
- `data/master_court_registry.csv` — Master canonical spine (`906` courts, `0` duplicate `court_id` or `official_satker_code`).
- `data/tables/` — 18 normalized relational tables (`general_courts.csv`, `religious_courts.csv`, `tun_courts.csv`, `military_courts.csv`, `court_hierarchy.csv`, `perkusi_execution_stats_2026.csv`, `satker_codes.csv`, `court_name_aliases.csv`, `source_register.csv`, `verification_log.csv`, etc.).
- `pipelines/` — Turnkey scripts for GitHub, Kaggle, LAPTAH crawler (`crawl_pu_laptah.py`), Qdrant, and Neo4j sync.
- `docs/` — Data quality & national court census reports.

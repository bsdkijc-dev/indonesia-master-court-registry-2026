#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Publish: indonesia-master-court-registry-2026 (v0.9)
# GitHub : git@github.com:bsdkijc-dev/indonesia-master-court-registry-2026.git
# Kaggle : https://www.kaggle.com/datasets/judicialcollege/indonesia-master-court-registry-2026
# ==============================================================================

REPO_NAME="indonesia-master-court-registry-2026"
GITHUB_USER="bsdkijc-dev"

echo "[1/3] Validating Master Court Registry v0.9 (906 courts + 382 PERKUSI PN)..."
python3 - << 'EOF'
import pandas as pd
df_mcr = pd.read_csv("data/master_court_registry.csv", dtype=str)
df_pu = pd.read_csv("data/tables/general_courts.csv", dtype=str)
df_pk = pd.read_csv("data/tables/perkusi_execution_stats_2026.csv", dtype=str)
assert len(df_mcr) == 906, f"Expected 906 courts, found {len(df_mcr)}"
assert len(df_pu) == 429, f"Expected 429 PU courts, found {len(df_pu)}"
assert len(df_pk) == 382, f"Expected 382 PERKUSI PNs, found {len(df_pk)}"
assert df_mcr["court_id"].duplicated().sum() == 0
assert df_pk["court_id"].duplicated().sum() == 0
print("[PASS] Verified 906 canonical courts, 429 PU courts (100%), and 382 PERKUSI execution records.")
EOF

echo "[2/3] Pushing to GitHub (${GITHUB_USER}/${REPO_NAME}) via SSH..."
if [ ! -d ".git" ]; then
  git init
  git branch -M main
fi
git config user.name "Cecep Mustafa"
git config user.email "cecepmustafa97@gmail.com"
git add .
git commit -m "feat(registry): release Indonesia Master Court Registry 2026 v0.9 (906 courts + 382 PERKUSI PN)" || true
git tag -a "v0.9.0-mcr2026" -m "National Court Census 2026 v0.9 + PERKUSI 2026" || true
git remote remove origin 2>/dev/null || true
git remote add origin "git@github.com:${GITHUB_USER}/${REPO_NAME}.git"
git push -u origin main --follow-tags

echo "[3/3] Pushing dataset to Kaggle (judicialcollege/indonesia-master-court-registry-2026)..."
chmod 600 ~/.kaggle/kaggle.json 2>/dev/null || true
if kaggle datasets status "judicialcollege/indonesia-master-court-registry-2026" >/dev/null 2>&1; then
  kaggle datasets version -p . -m "Release v0.9.0: 906 verified Indonesian courts + 2026 PERKUSI 382 PN statistics" -r zip
else
  kaggle datasets create -p . -r zip
fi

echo "[DONE] GitHub and Kaggle sync completed for v0.9.0!"

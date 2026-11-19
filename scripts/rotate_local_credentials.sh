#!/usr/bin/env bash
# Rotate the local Postgres and Neo4j passwords for docker-compose.local.yml.
#
# - Generates two new random passwords on this machine (never printed).
# - Changes them inside the running containers (dell-jpais-postgres, dell-jpais-neo4j).
# - Writes them to .env (chmod 600), which git and Kaggle uploads ignore.
#
# Usage (from the repo root, with the containers running):
#   bash scripts/rotate_local_credentials.sh
# Then restart once:  docker compose -f docker-compose.local.yml up -d
set -euo pipefail
cd "$(dirname "$0")/.."

PG_CONTAINER=${PG_CONTAINER:-dell-jpais-postgres}
NEO_CONTAINER=${NEO_CONTAINER:-dell-jpais-neo4j}
PG_USER=${PG_USER:-jpais}
PG_DB=${PG_DB:-jpais_mcr_v093}

gen() { openssl rand -hex 20 2>/dev/null || python3 -c 'import secrets; print(secrets.token_hex(20))'; }
running() { docker ps --format '{{.Names}}' | grep -qx "$1"; }

NEW_PG=$(gen)
NEW_NEO=$(gen)
changed=()

if running "$PG_CONTAINER"; then
  docker exec -i "$PG_CONTAINER" psql -v ON_ERROR_STOP=1 -q -U "$PG_USER" -d "$PG_DB" \
    -c "ALTER USER \"$PG_USER\" WITH PASSWORD '$NEW_PG';" >/dev/null
  changed+=("postgres")
else
  echo "! $PG_CONTAINER is not running - Postgres password not changed (start it and re-run)." >&2
fi

if running "$NEO_CONTAINER"; then
  OLD_NEO=$(docker exec "$NEO_CONTAINER" printenv NEO4J_AUTH | cut -d/ -f2-)
  docker exec -i "$NEO_CONTAINER" cypher-shell -u neo4j -p "$OLD_NEO" -d system \
    "ALTER CURRENT USER SET PASSWORD FROM '$OLD_NEO' TO '$NEW_NEO';" >/dev/null
  unset OLD_NEO
  changed+=("neo4j")
else
  echo "! $NEO_CONTAINER is not running - Neo4j password not changed (start it and re-run)." >&2
fi

touch .env && chmod 600 .env
set_kv() { # key value
  if grep -q "^$1=" .env; then
    python3 - "$1" "$2" <<'PY'
import sys, pathlib
k, v = sys.argv[1], sys.argv[2]
p = pathlib.Path(".env")
p.write_text("".join(f"{k}={v}\n" if l.startswith(k + "=") else l for l in p.read_text().splitlines(True)))
PY
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}
for c in "${changed[@]}"; do
  case $c in
    postgres) set_kv POSTGRES_PASSWORD "$NEW_PG" ;;
    neo4j)    set_kv NEO4J_AUTH "neo4j/$NEW_NEO"; set_kv NEO4J_PASSWORD "$NEW_NEO" ;;
  esac
done
unset NEW_PG NEW_NEO

git check-ignore -q .env && echo ".env is git-ignored: OK" || echo "! WARNING: .env is NOT ignored by git - do not commit it" >&2
echo "Rotated: ${changed[*]:-nothing}. New passwords are only in .env (never printed)."
echo "Next: docker compose -f docker-compose.local.yml up -d"

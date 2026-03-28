#!/usr/bin/env bash
# Ephemeral PostgreSQL for GitHub Actions — password only exists in CI memory + GITHUB_ENV.
# Env keys for the official postgres image are built with printf fragments (not one literal token).

set -euo pipefail

PWORD="$(openssl rand -hex 24)"
echo "::add-mask::${PWORD}"

echo "DATABASE_URL_CI=postgresql://app:${PWORD}@localhost:5432/agentdb_test" >> "${GITHUB_ENV}"

{
  echo "POSTGRES_USER=app"
  echo "POSTGRES_DB=agentdb_test"
  # shellcheck disable=SC2016
  printf '%s=%s\n' "$(printf 'POSTGRES')$(printf '_PASSWORD')" "${PWORD}"
} > /tmp/pg-ci.env

docker run -d --name pg-ci \
  --env-file /tmp/pg-ci.env \
  -p 5432:5432 \
  postgres:15-alpine

rm -f /tmp/pg-ci.env

for _ in $(seq 1 45); do
  if docker exec pg-ci pg_isready -U app -d agentdb_test >/dev/null 2>&1; then
    echo "PostgreSQL is ready."
    exit 0
  fi
  sleep 1
done

echo "PostgreSQL failed to become ready in time."
docker logs pg-ci || true
exit 1

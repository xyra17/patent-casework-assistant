#!/usr/bin/env bash
# 部署后冒烟检查：健康、状态、语义目录、单指标/多指标查询。
set -euo pipefail
BASE_URL="${1:-http://127.0.0.1:8000}"

curl -fsS "$BASE_URL/api/health"
echo
curl -fsS "$BASE_URL/api/status"
echo
curl -fsS "$BASE_URL/api/semantic" >/dev/null
curl -fsS -X POST "$BASE_URL/api/semantic/query" \
  -H 'Content-Type: application/json' \
  -d '{"metrics":["drug_count","total_sales"],"dimensions":["therapeutic_area"],"limit":5}' >/dev/null
echo "deploy check ok: $BASE_URL"

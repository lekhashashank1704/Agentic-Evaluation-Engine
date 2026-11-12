#!/usr/bin/env bash
# Run every demonstration scenario against a running API (default http://localhost:8000).
set -euo pipefail
API="${API:-http://localhost:8000}"
for s in breach_and_learn healthy_release critical_risk security_block stagnation regression budget_exceeded protected_path approval_invalidation; do
  printf '%-24s ' "$s"
  curl -fsS -X POST "$API/api/demo/$s" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["execution_id"], d["status"], d["decision"])'
done

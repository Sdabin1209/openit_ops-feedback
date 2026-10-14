#!/usr/bin/env bash
# 이미지를 빌드하고 컨테이너를 띄워 /healthz, /readyz, 수집(ACCEPTED/DUPLICATE)을 확인한다. 실패하면 0이 아닌 값으로 끝난다.
set -euo pipefail
cd "$(dirname "$0")/.."
DOCKER=docker
if ! docker info >/dev/null 2>&1; then DOCKER=docker.exe; fi
IMG=ops-feedback-ingest:smoke-$$
NAME=ops-feedback-smoke-$$
COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
bash scripts/build_wheels.sh >/dev/null
CV=$(cat build/wheels/BOUNDARY_TAG)
$DOCKER build -q -t $IMG --build-arg COMMIT="$COMMIT" --build-arg CONTRACTS_VERSION="$CV" .
trap '$DOCKER rm -f $NAME >/dev/null 2>&1 || true; $DOCKER rmi -f $IMG >/dev/null 2>&1 || true' EXIT
$DOCKER run -d --name $NAME -p 127.0.0.1::8080 -e OPS_INGEST_TOKEN=smoke-token $IMG >/dev/null
PORT=$($DOCKER port $NAME 8080/tcp | head -1 | tr -d '\r' | sed 's/.*://')
echo "host port: $PORT"
for i in $(seq 1 30); do
  curl -fsS http://127.0.0.1:$PORT/healthz >/dev/null 2>&1 && break
  sleep 1
done
echo "healthz: $(curl -fsS http://127.0.0.1:$PORT/healthz)"
echo "readyz:  $(curl -fsS http://127.0.0.1:$PORT/readyz)"
[ "$($DOCKER exec $NAME id -u | tr -d '\r')" != "0" ] || { echo "root로 실행 중"; exit 1; }
BODY=$(python3 - <<'PY'
import json
keys="session_id turn_id contract_version frontend_message intervention_decision orchestrator_input parser_input parser_output normalizer_input normalizer_output judge_input judge_output record_writer_input record_writer_output planner_input planner_output generator_input generator_output frontend_input staff_console_input dispatch_input dispatch_output orchestrator_output session_before session_after log_entry persistence_load_request persistence_load_result persistence_commit_request persistence_commit_result".split()
t={k:None for k in keys}; t.update(session_id="s-smoke", turn_id="t-smoke", contract_version=1)
print(json.dumps({"pipeline_schema_version":"1.0","release_id":"rel-smoke","runtime_contract_version":1,"session_id":"s-smoke","turn_id":"t-smoke","sent_at":"2026-10-15T00:00:00Z","trace":t}))
PY
)
post() { curl -sS -X POST -H "Authorization: Bearer smoke-token" -H "Content-Type: application/json" -d "$BODY" http://127.0.0.1:$PORT/v1/traces; }
R1=$(post); R2=$(post)
echo "send1: $R1"; echo "send2: $R2"
echo "$R1" | grep -q '"ACCEPTED"' && echo "$R2" | grep -q '"DUPLICATE"' && echo "$R1" | grep -q '"pipeline_schema_version": "1.1"' || { echo "수집 확인 실패"; exit 1; }
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -d '{}' http://127.0.0.1:$PORT/v1/traces)
[ "$code" = "403" ] || { echo "토큰 없는 요청이 거부되지 않음: $code"; exit 1; }
echo "SMOKE OK"

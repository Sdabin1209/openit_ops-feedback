#!/usr/bin/env bash
# 이미지를 빌드하고 컨테이너를 띄워 /healthz, /readyz, 수집(ACCEPTED/DUPLICATE)을 확인한다. 실패하면 0이 아닌 값으로 끝난다.
set -euo pipefail
cd "$(dirname "$0")/.."
DOCKER=${DOCKER:-docker}     # 실행 파일 이름만 바꿀 수 있다(예: DOCKER=podman). 다른 분기는 없다.
if ! $DOCKER info >/dev/null 2>&1; then
  echo "오류: $DOCKER 데몬에 연결할 수 없다. 데몬이 실행 중이고 이 사용자가 접근할 수 있는지 확인하라." >&2
  exit 2
fi
IMG=ops-feedback-ingest:smoke-$$
NAME=ops-feedback-smoke-$$
COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
bash scripts/build_wheels.sh >/dev/null
CV=$(cat build/wheels/BOUNDARY_TAG)
$DOCKER build -q -t $IMG --build-arg COMMIT="$COMMIT" --build-arg CONTRACTS_VERSION="$CV" \
  --build-arg SURVEY_SCHEMA_VERSION="$(cat src/ops_feedback/_schemas/survey/v1/CONTRACT_VERSION)" .
trap '$DOCKER rm -f $NAME >/dev/null 2>&1 || true; $DOCKER rmi -f $IMG >/dev/null 2>&1 || true' EXIT
# 비밀은 환경 변수가 아니라 파일로 준다(NAME_FILE, D84). docker inspect/exec env에 값이 보이지 않아야 한다.
SECDIR=$(mktemp -d); chmod 755 $SECDIR
printf 'smoke-token' > $SECDIR/ingest; printf 'synthetic-data=read-token' > $SECDIR/read; printf 'reviewer=review-token' > $SECDIR/review; printf 'smoke-salt' > $SECDIR/salt
chmod 644 $SECDIR/*
trap '$DOCKER rm -f $NAME >/dev/null 2>&1 || true; $DOCKER rmi -f $IMG >/dev/null 2>&1 || true; rm -rf $SECDIR' EXIT
$DOCKER run -d --name $NAME -p 127.0.0.1::8080 -v $SECDIR:/run/secrets:ro \
  -e OPS_INGEST_TOKEN_FILE=/run/secrets/ingest -e OPS_READ_TOKENS_FILE=/run/secrets/read -e OPS_REVIEW_TOKENS_FILE=/run/secrets/review \
  -e OPS_DEID_SALT_FILE=/run/secrets/salt $IMG >/dev/null
if $DOCKER inspect $NAME | grep -qE 'smoke-token|read-token|review-token|smoke-salt'; then echo "inspect에 비밀 값이 보인다"; exit 1; fi
if $DOCKER exec $NAME env | grep -qE 'smoke-token|read-token|review-token|smoke-salt'; then echo "exec env에 비밀 값이 보인다"; exit 1; fi
PORT=$($DOCKER port $NAME 8080/tcp | head -1 | sed 's/.*://')
echo "host port: $PORT"
for i in $(seq 1 30); do
  curl -fsS http://127.0.0.1:$PORT/healthz >/dev/null 2>&1 && break
  sleep 1
done
echo "healthz: $(curl -fsS http://127.0.0.1:$PORT/healthz)"
echo "readyz:  $(curl -fsS http://127.0.0.1:$PORT/readyz)"
[ "$($DOCKER exec $NAME id -u)" != "0" ] || { echo "root로 실행 중"; exit 1; }
# 스키마 버전은 설치된 계약 패키지의 상수에서 읽는다(리터럴을 적지 않는다)
PYBIN=${PYBIN:-.venv/bin/python}
PSV=$($PYBIN -c 'from boundary_contracts import PIPELINE_SCHEMA_VERSION as v;print(v)')
BODY=$(PSV="$PSV" $PYBIN - <<'PY'
import json, os
keys="session_id turn_id contract_version frontend_message intervention_decision orchestrator_input extract_input extract_output extract_attempts extract_status extract_protections validation_ok validation_violations response_input response_output response_outcome response_protections frontend_input staff_console_input dispatch_input dispatch_output orchestrator_output session_before session_after log_entry persistence_load_request persistence_load_result persistence_commit_request persistence_commit_result".split()
t={k:None for k in keys}; t.update(session_id="s-smoke", turn_id="t-smoke", contract_version=1)
print(json.dumps({"pipeline_schema_version":os.environ["PSV"],"release_id":"rel-smoke","runtime_contract_version":1,"session_id":"s-smoke","turn_id":"t-smoke","sent_at":"2026-10-15T00:00:00Z","trace":t}))
PY
)
post() { curl -sS -X POST -H "Authorization: Bearer smoke-token" -H "Content-Type: application/json" -d "$BODY" http://127.0.0.1:$PORT/v1/traces; }
R1=$(post); R2=$(post)
echo "send1: $R1"; echo "send2: $R2"
echo "$R1" | grep -q '"ACCEPTED"' && echo "$R2" | grep -q '"DUPLICATE"' && echo "$R1" | grep -q "\"pipeline_schema_version\": \"$PSV\""  || { echo "수집 확인 실패"; exit 1; }
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -d '{}' http://127.0.0.1:$PORT/v1/traces)
[ "$code" = "403" ] || { echo "토큰 없는 요청이 거부되지 않음: $code"; exit 1; }
rd() { curl -s -o /tmp/smoke-$$.out -w '%{http_code}' -H "Authorization: Bearer $1" "http://127.0.0.1:$PORT$2"; }
[ "$(rd read-token '/v1/cases?latest_only=true&limit=5')" = "200" ] && grep -q '"items": \[\]' /tmp/smoke-$$.out || { echo "읽기 호출 실패"; exit 1; }
[ "$(rd read-token /v1/cases/nope)" = "404" ] || { echo "없는 사례가 404가 아님"; exit 1; }
[ "$(rd smoke-token /v1/cases)" = "403" ] || { echo "수집 토큰으로 읽기가 거부되지 않음"; exit 1; }
c=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer read-token" -d "$BODY" http://127.0.0.1:$PORT/v1/traces)
[ "$c" = "403" ] || { echo "읽기 토큰으로 수집이 거부되지 않음: $c"; exit 1; }
rm -f /tmp/smoke-$$.out
for k in service revision contracts.boundary contracts.survey-schema turn-engine model-runtime; do
  $DOCKER image inspect $IMG --format "{{ index .Config.Labels \"io.openit.$k\" }}" | grep -q . || { echo "라벨 io.openit.$k 없음"; exit 1; }
done
# 검토 게시: 실패 턴을 보내고 컨테이너 안에서 잡을 한 번 돌려 사례를 만든 뒤 HTTP로 검토한다
FAIL=$(echo "$BODY" | python3 -c 'import json,sys
b=json.load(sys.stdin); b["turn_id"]=b["trace"]["turn_id"]="t-fail"; b["trace"]["response_output"]="x"; b["trace"]["response_outcome"]="fallback"; print(json.dumps(b))')
curl -fsS -X POST -H "Authorization: Bearer smoke-token" -d "$FAIL" http://127.0.0.1:$PORT/v1/traces >/dev/null
sed 's#data/log.db#/data/log.db#; s#data/failures.db#/data/failures.db#' config/job.example.yaml > /tmp/smoke-job-$$.yaml
$DOCKER cp /tmp/smoke-job-$$.yaml $NAME:/tmp/job.yaml; rm -f /tmp/smoke-job-$$.yaml
$DOCKER exec $NAME python -m ops_feedback.job --config /tmp/job.yaml --no-rollback | tee /tmp/smoke-job-$$.out
CID=$(curl -fsS -H "Authorization: Bearer read-token" "http://127.0.0.1:$PORT/v1/cases?review_status=PENDING" | python3 -c 'import json,sys;print(json.load(sys.stdin)["items"][0]["case_id"])')
rv() { curl -s -o /tmp/smoke-$$.out -w '%{http_code}' -X POST -H "Authorization: Bearer $1" ${3:+-H "X-Openit-User: $3"} -d "$2" "http://127.0.0.1:$PORT/v1/cases/$CID/review"; }
[ "$(rv read-token '{"review_status":"DISMISSED"}')" = "403" ] || { echo "읽기 토큰으로 검토가 거부되지 않음"; exit 1; }
[ "$(rv review-token '{"review_status":"DISMISSED"}')" = "403" ] || { echo "신원 없는 검토 게시가 거부되지 않음(D88/U7)"; exit 1; }
[ "$(rv review-token '{"review_status":"DISMISSED"}' smoke.reviewer)" = "201" ] || { echo "검토 게시 실패"; exit 1; }
[ "$(rv review-token '{"review_status":"DISMISSED"}' smoke.reviewer)" = "200" ] || { echo "검토 재전송이 멱등이 아님"; exit 1; }
curl -fsS -H "Authorization: Bearer read-token" "http://127.0.0.1:$PORT/v1/cases/$CID" | grep -q '"version": 2' || { echo "새 version이 읽히지 않음"; exit 1; }
rm -f /tmp/smoke-$$.out /tmp/smoke-job-$$.out
echo "SMOKE OK"

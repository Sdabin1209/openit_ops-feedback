#!/usr/bin/env bash
# 일회용 PostgreSQL 16 컨테이너를 띄워 그 위에서 시험을 돌리고, 끝나면 컨테이너를 내린다. PostgreSQL 시험이 건너뛰어지지 않는다.
#   scripts/dev-postgres.sh                 # 전체 pytest
#   scripts/dev-postgres.sh -k timeout -x   # pytest 인자를 그대로 넘긴다
# 호스트 포트는 Docker가 배정한다(고정 포트 없음). 컨테이너 이름에 리포지토리 이름과 PID를 넣는다. 종료 코드는 pytest의 것이다.
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/docker-env.sh                      # DOCKER_CONFIG·DOCKER_HOST 규칙(사용자 ~/.docker 에 기대지 않는다)
DOCKER=${DOCKER:-docker}
PY=${PYTHON:-.venv/bin/python}
if ! $DOCKER info >/dev/null 2>&1; then echo "오류: $DOCKER 데몬에 연결할 수 없다" >&2; exit 2; fi
# 시험용 PostgreSQL 이미지는 digest 로 고정한다(재현 가능한 시험). 값은 2026-11-05 이 PC 의 postgres:16 RepoDigest. db/Dockerfile 의 POSTGRES_IMAGE 와 같은 값이다(시험이 일치를 본다).
PG_IMAGE=postgres:16@sha256:1beaa3084eee2676f20a9cd9928f0bf2c5bf674f8e49f81017912183d377c61c
NAME=ops-feedback-devpg-$$
PW=$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')     # 일회용 비밀번호(출력하지 않는다)
trap '$DOCKER rm -f $NAME >/dev/null 2>&1 || true' EXIT
$DOCKER run -d --name $NAME -e POSTGRES_USER=opsu -e POSTGRES_PASSWORD="$PW" -e POSTGRES_DB=opsdb -p 127.0.0.1::5432 $PG_IMAGE >/dev/null
# 준비 확인은 TCP(-h 127.0.0.1)로 한다. 이전 결함(고쳤다): 공식 이미지는 초기화 동안 소켓만 여는 임시 서버를 먼저 띄우고 끝나면 재시작하는데, 소켓 pg_isready 는 임시 서버에서도 통과했고
# 그 뒤 고정 `sleep 2` 가 부하 때 재시작보다 짧을 수 있었다(시험이 재시작 중에 접속하는 경쟁). TCP 확인은 최종 서버가 떴을 때만 통과하므로 고정 대기가 필요 없다.
# 상한 120초: 한가한 PC 에서 잰 준비 시간은 1.3~2.5초(3회)다. 별개로, 이전 40초 상한 안에 준비되지 않아 종료 코드 2가 난 적이 1번 있었다. 그 원인은 판정하지 못했다(같은 이미지는 수동으로 정상 기동했고 당시 다른 작업이 도는 부하였다).
# 그래서 관측한 최악(40초 초과, 정확한 값은 모름)의 3배를 상한으로 둔다(D125 의 측정 최악 + 여유 원칙의 근사). 컨테이너가 이미 끝났으면 기다리지 않고 로그 끝을 낸다.
READY_LIMIT_SECONDS=120
until $DOCKER exec $NAME pg_isready -h 127.0.0.1 -U opsu -d opsdb >/dev/null 2>&1; do
  if [ "$($DOCKER inspect -f '{{.State.Running}}' $NAME 2>/dev/null)" != true ]; then echo "오류: PostgreSQL 컨테이너가 끝났다" >&2; $DOCKER logs --tail 20 $NAME >&2 || true; exit 2; fi
  if [ "$SECONDS" -ge "$READY_LIMIT_SECONDS" ]; then echo "오류: PostgreSQL이 ${READY_LIMIT_SECONDS}초 안에 뜨지 않았다" >&2; $DOCKER logs --tail 20 $NAME >&2 || true; exit 2; fi
  sleep 0.5
done
PORT=$($DOCKER port $NAME 5432/tcp | head -1 | sed 's/.*://')
TEST_DATABASE_URL="postgresql://opsu:$PW@127.0.0.1:$PORT/opsdb" TEST_PG_CONTAINER=$NAME "$PY" -m pytest -q -p no:cacheprovider "$@"

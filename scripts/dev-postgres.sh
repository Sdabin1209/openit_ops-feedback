#!/usr/bin/env bash
# 일회용 PostgreSQL 16 컨테이너를 띄워 그 위에서 시험을 돌리고, 끝나면 컨테이너를 내린다. PostgreSQL 시험(110건)이 건너뛰어지지 않는다.
#   scripts/dev-postgres.sh                 # 전체 pytest
#   scripts/dev-postgres.sh -k timeout -x   # pytest 인자를 그대로 넘긴다
# 호스트 포트는 Docker가 배정한다(고정 포트 없음). 컨테이너 이름에 리포지토리 이름과 PID를 넣는다. 종료 코드는 pytest의 것이다.
set -euo pipefail
cd "$(dirname "$0")/.."
DOCKER=${DOCKER:-docker}
PY=${PYTHON:-.venv/bin/python}
if ! $DOCKER info >/dev/null 2>&1; then echo "오류: $DOCKER 데몬에 연결할 수 없다" >&2; exit 2; fi
# 시험용 PostgreSQL 이미지는 digest 로 고정한다(재현 가능한 시험). 값은 2026-11-05 이 PC 의 postgres:16 RepoDigest. db/Dockerfile 의 POSTGRES_IMAGE 와 같은 값이다(시험이 일치를 본다).
PG_IMAGE=postgres:16@sha256:1beaa3084eee2676f20a9cd9928f0bf2c5bf674f8e49f81017912183d377c61c
NAME=ops-feedback-devpg-$$
PW=$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')     # 일회용 비밀번호(출력하지 않는다)
trap '$DOCKER rm -f $NAME >/dev/null 2>&1 || true' EXIT
$DOCKER run -d --name $NAME -e POSTGRES_USER=opsu -e POSTGRES_PASSWORD="$PW" -e POSTGRES_DB=opsdb -p 127.0.0.1::5432 $PG_IMAGE >/dev/null
for i in $(seq 1 40); do $DOCKER exec $NAME pg_isready -U opsu -d opsdb >/dev/null 2>&1 && break; sleep 1; done
$DOCKER exec $NAME pg_isready -U opsu -d opsdb >/dev/null 2>&1 || { echo "오류: PostgreSQL이 뜨지 않았다" >&2; exit 2; }
sleep 2     # 초기화 재시작이 끝나기를 기다린다
PORT=$($DOCKER port $NAME 5432/tcp | head -1 | sed 's/.*://')
TEST_DATABASE_URL="postgresql://opsu:$PW@127.0.0.1:$PORT/opsdb" TEST_PG_CONTAINER=$NAME "$PY" -m pytest -q -p no:cacheprovider "$@"

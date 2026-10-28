#!/usr/bin/env bash
# 지정 태그 시점의 boundary-contracts와 문진 스키마 패키지 소스를 git archive로 꺼내 wheel을 만든다(docker 안 ssh가 어려워서).
# 결과(build/wheels)는 빌드 산출물이라 커밋하지 않는다.
set -euo pipefail
cd "$(dirname "$0")/.."
TAG=${BOUNDARY_TAG:?사용법: BOUNDARY_TAG=<태그> scripts/build_wheels.sh (기준 태그는 ../deploy/scripts/lib.sh가 정본이고 deploy가 넘긴다)}
# 문진 스키마 태그: requirements.txt 핀이 정본이다(여기에 값을 따로 적지 않는다).
SURVEY_TAG=${SURVEY_SCHEMA_TAG:-$(sed -n 's/.*openit_survey-service\.git@\([^#]*\)#.*/\1/p' requirements.txt | head -1)}
[ -n "$SURVEY_TAG" ] || { echo "requirements.txt에서 문진 스키마 태그를 읽지 못했다" >&2; exit 1; }
# 왜: 이웃 리포 위치를 바꿀 수 있게 한다(git worktree 같은 곳에서 돌릴 때). 기본은 형제 폴더다.
WS=${WORKSPACE_DIR:-..}
rm -rf build/wheels build/src && mkdir -p build/wheels build/src/survey-schema
git -C "$WS/boundary-contracts" archive "$TAG" | tar -x -C build/src
git -C "$WS/survey-service" archive "$SURVEY_TAG" schema | tar -x -C build/src/survey-schema
PIP=${PIP:-.venv/bin/pip}
$PIP wheel -q --no-deps --no-build-isolation -w build/wheels build/src
$PIP wheel -q --no-deps --no-build-isolation -w build/wheels build/src/survey-schema/schema
echo "$TAG" > build/wheels/BOUNDARY_TAG
tr -d '[:space:]' < build/src/survey-schema/schema/CONTRACT_VERSION > build/wheels/SURVEY_SCHEMA_VERSION      # 라벨 io.openit.contracts.survey-schema(정수 주 버전)
ls build/wheels

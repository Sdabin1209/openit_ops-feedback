#!/usr/bin/env bash
# 지정 태그 시점의 boundary-contracts 소스를 git archive로 꺼내 wheel을 만든다(docker 안 ssh가 어려워서).
set -euo pipefail
cd "$(dirname "$0")/.."
TAG=${BOUNDARY_TAG:?사용법: BOUNDARY_TAG=<태그> scripts/build_wheels.sh (기준 태그는 ../deploy/scripts/lib.sh가 정본이고 deploy가 넘긴다)}
rm -rf build/wheels build/src && mkdir -p build/wheels build/src
git -C ../boundary-contracts archive "$TAG" | tar -x -C build/src
PIP=${PIP:-.venv/bin/pip}
$PIP wheel -q --no-deps -w build/wheels build/src
echo "$TAG" > build/wheels/BOUNDARY_TAG
ls build/wheels

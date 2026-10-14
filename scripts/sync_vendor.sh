#!/usr/bin/env bash
# 읽기 전용 이웃(../boundary-contracts, ../survey-service/schema)의 스냅샷을 이 리포지토리에 복사한다.
# 컨테이너 빌드가 리포지토리 밖을 볼 수 없기 때문이다. 원본은 수정하지 않는다.
set -euo pipefail
cd "$(dirname "$0")/.."
B=../boundary-contracts; S=../survey-service/schema
cp "$B"/schemas/*.json src/ops_feedback/_schemas/boundary/
cp "$S"/schemas/TurnTrace.json src/ops_feedback/_schemas/survey/v1/TurnTrace.json
cp "$S"/CONTRACT_VERSION src/ops_feedback/_schemas/survey/v1/CONTRACT_VERSION
rm -rf vendor/boundary-contracts/boundary_contracts
cp "$B"/pyproject.toml vendor/boundary-contracts/
cp -r "$B"/boundary_contracts vendor/boundary-contracts/
find vendor -name __pycache__ -prune -exec rm -rf {} +
echo synced

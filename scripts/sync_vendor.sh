#!/usr/bin/env bash
# 문진 스키마(읽기 전용 이웃)의 스냅샷을 이 리포지토리로 복사한다. 컨테이너 빌드가 리포지토리 밖을 볼 수 없기 때문이다.
# 경계 계약 스키마와 코드는 boundary-contracts 패키지가 가지므로 복사하지 않는다.
# 정수 버전 폴더는 v1 유지(D79: 같은 정수 안에서 TurnTrace가 바뀌었다).
# 문진 스키마를 설치 가능한 패키지로 내는 일은 survey-service 과제다(그때까지 이 방식 유지).
set -euo pipefail
cd "$(dirname "$0")/.."
S=../survey-service/schema
cp "$S"/schemas/TurnTrace.json src/ops_feedback/_schemas/survey/v1/TurnTrace.json
cp "$S"/CONTRACT_VERSION src/ops_feedback/_schemas/survey/v1/CONTRACT_VERSION
echo synced

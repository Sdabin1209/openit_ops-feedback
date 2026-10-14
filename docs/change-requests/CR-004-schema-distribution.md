# CR-004 스키마 배포 방식: 경계 계약 스키마와 문진 스키마를 설치 가능한 형태로

- 요청자: ops-feedback
- 대상: `boundary-contracts/pyproject.toml`, `survey-service/schema`
- 처리 결과(헤드 회신): 결정: boundary 스키마는 패키지(v1.0.0)로 해결. 문진 스키마 패키지화는 survey-service 과제, 그때까지 스냅샷(survey/v<정수>/) 유지.
- 이전 상태: 헤드 결정 필요

## 부족한 점
1. `boundary_contracts` 패키지는 `schemas/*.json`을 담지 않는다(`packages = ["boundary_contracts"]`). `pip install`한 쪽은 레코드를 검증할 스키마가 없다.
2. 문진 스키마는 "라이브러리로" 쓴다고 했지만 설치 가능한 패키지가 없고, 컨테이너 빌드는 리포지토리 밖(`../survey-service/schema`)을 볼 수 없다.
3. 수집은 `runtime_contract_version`별 TurnTrace로 검증해야 한다(여러 정수 버전 동시 지원). 버전별 스키마를 어떻게 얻는지 정해진 바가 없다.

## 현재 대응(임시)
`scripts/sync_vendor.sh`가 두 스키마와 `boundary_contracts` 코드를 이 리포지토리로 복사한다(`src/ops_feedback/_schemas/`, `vendor/boundary-contracts/`). `tests/test_schema_sync.py`가 원본과 달라지면 실패한다. 문진 스키마는 `survey/v<정수>/` 폴더 구조로 두어 다중 버전을 지원할 수 있게 했다.

## 제안
- `boundary_contracts`에 스키마 JSON을 package data로 포함하고 `load_schema(name)`를 제공한다.
- 문진 스키마를 Git 태그가 붙은 설치 가능 패키지로 배포하고 정수 버전별 스키마 디렉터리를 포함한다.
- 두 가지 모두 Git 태그가 정해지면 Dockerfile의 복사를 태그 설치로 바꾼다.

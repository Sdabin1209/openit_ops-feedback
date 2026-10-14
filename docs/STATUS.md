# STATUS

날짜: 2026-10-15. 첫 구간 완료 상태(아래 시험은 이 날짜에 실제로 돌린 것).

## 한 일

- design.md, boundary.md, runbook.md, CHANGELOG.md, 변경 요청 5건.
- I6, I7의 SQLite·메모리 구현과 같은 적합성 시험. 수집은 envelope.runtime_contract_version의 TurnTrace로 검증.
- 비식별 처리기(허용 목록, 설정 정책), 규칙 탐지기(교체 가능), 설정 분류, 검토 큐, 롤백 요청 생성(I4, I5는 Protocol 인자, 시험 대역은 tests/fakes.py).
- 주기 잡 명령, 설정 예시, 입출력 예시(`examples/`).
- Dockerfile, `scripts/smoke.sh`(빌드, `/healthz`, `/readyz`, 수집, 인증 통과 확인).

## 시험

- `.venv/bin/python -m pytest -q`: 전부 통과(85건).
- `scripts/smoke.sh`: `SMOKE OK`.

## 남은 일

- 사용자 결정 대기: 비식별 기준(O 번호 없음, 브리프 5절), 보관 기간, 분류체계 O11, 허용 중단 시간 O16, 롤백 기준 수치.
- 지표 대시보드, 피드백 배포기, 검토 UI, 사람 신고(FEEDBACK/HUMAN) 탐지 경로, 모델 기반 탐지기.
- I4/I5 실제 SDK 연결(release-gate가 배포하면 `release_reader`/`gate_service` 팩토리 설정).
- 실패 사례·비식별 trace 보관/삭제 정책.
- Git 태그 기반 의존 설치(현재는 `scripts/sync_vendor.sh` 사본, CR-004). 이미지 CI 게시와 digest 제출(D24)은 CI가 생기면.
- list_cases를 SQL 필터로(사례가 많아질 때).

## 막힌 것

없음. 확정이 필요한 경계 사항은 CR-001~005.

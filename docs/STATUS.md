# STATUS

날짜: 2026-10-15. 첫 구간 완료 상태(아래 "한 일"·"시험"은 이 날짜 기준의 이력이다). 현재 시험 수는 맨 아래 "2026-10-27 현재 상태" 절을 본다.

## 한 일

- design.md, boundary.md, runbook.md, CHANGELOG.md, 변경 요청 5건.
- I6, I7의 SQLite·메모리 구현과 같은 적합성 시험. 수집은 envelope.runtime_contract_version의 TurnTrace로 검증.
- 비식별 처리기(허용 목록, 설정 정책), 규칙 탐지기(교체 가능), 설정 분류, 검토 큐, 롤백 요청 생성(I4, I5는 Protocol 인자, 시험 대역은 tests/fakes.py).
- 주기 잡 명령, 설정 예시, 입출력 예시(`examples/`).
- Dockerfile, `scripts/smoke.sh`(빌드, `/healthz`, `/readyz`, 수집, 인증 통과 확인).

## 시험

- `.venv/bin/python -m pytest -q`: 전부 통과(85건, 2026-10-15 당시. 현재는 195건).
- 헤드 회신 후속(0.1.1) 반영 후 재실행 결과는 CHANGELOG 0.1.1과 최신 커밋 기준.
- `scripts/smoke.sh`: `SMOKE OK`.

## 검토 면

- `POST /v1/cases/{id}/review` 구현됨(0.5.0). 검토 화면(UI)은 아직 없고 이 면의 클라이언트로 붙인다. 아직 없는 것: `boundary_contracts.clients`의 검토 클라이언트.

## 정리 완료

- 자체 `HttpTraceIngest`(`client.py`)는 삭제했다. 사용처는 `boundary_contracts.clients.HttpTraceIngest`(v1.2.0)다. I4 읽기도 `boundary_contracts.clients:HttpReleaseReader`를 설정의 팩토리로 고른다(tests/test_http_clients.py로 확인).
- 대역 `tests/fakes.py`는 단위 시험용으로 남긴다.

## 남은 일

- 사용자 결정 대기: 비식별 기준(대장 번호 없음, 브리프 5절), 보관 기간, 분류체계 O11(대장 번호 없음), 허용 중단 시간 O16(대장 번호 없음), 롤백 기준 수치.
- 지표 대시보드, 피드백 배포기, 검토 UI, 사람 신고(FEEDBACK/HUMAN) 탐지 경로, 모델 기반 탐지기.
- I4/I5 실제 SDK 연결(release-gate가 배포하면 `release_reader`/`gate_service` 팩토리 설정).
- 실패 사례·비식별 trace 보관/삭제 정책.
- 문진 스키마 패키지화(survey-service 과제, 현재 스냅샷). 이미지 CI 게시와 digest 제출(D24)은 CI가 생기면.

## 막힌 것

2026-10-15 당시: 없음. 확정이 필요한 경계 사항은 CR-001~005. 이 문장은 현재 상태가 아니다. 현재는 맨 아래 "2026-10-27 현재 상태"를 본다.

## 2026-10-27 새 TurnTrace 따라가기 (브랜치 follow-turntrace-v2)

한 일은 CHANGELOG 0.9.0. 남은 것·미정:
- [제안] `extract_protections`·`response_protections`·`extract_input.state`·`response_input.next`는 스키마상 자유 객체라 통째로 비워진다. 규칙 코드별 건수는 개인정보가 아니라 남길 가치가 있으나(키 이름도 값일 수 있어 지금은 비움) 허용 정의 목록 방식으로 남기는 것은 사용자 결정이 필요하다.
- [미정] `action`·`next_item` 등 모델이 낸 문자열을 `keep_string_keys`로 남긴다. 위반 턴에서는 임의 문자열일 수 있고 잔존 패턴 검사만 거친다.
- [미정] `REASK_STREAK` 임계값, `EXTRACT_FAILED`의 허용 action(HOLD·SKIP 등).
- 이전 정책 지문으로 비식별해 둔 기존 사례는 그대로다(재처리 안 함).

## 2026-10-27 boundary-contracts v2.0.0 이전 (브랜치 migrate-contracts-2.0.0)

정규화·판정 결합 확인(코드로 읽은 것):
- 비식별 처리기(`deid.py`)는 TurnTrace 정의를 걷는 범용 허용 목록 방식이라 정규화·판정 단계 코드에 묶여 있지 않다. 단, 설정 예시의 `mask_defs: [Value]`, `drop_defs: [QuestionBank]`, `keep_string_keys`는 v1 문진 스키마(`_schemas/survey/v1/TurnTrace.json`)의 정의·키 이름이다. 새 TurnTrace가 오면 정책 설정을 다시 맞춰야 한다.
- 실패 유형 분류(`Classifier`)는 규칙 ID -> failure_type 설정 대응이고 코드에 유형 값이 없다. 정규화·판정과 무관하다.
- 탐지 규칙(`detect.py`) 중 v1 trace 필드를 읽는 것: `GENERATOR_FALLBACK`(`generator_output.is_fallback`), `PARSER_EMPTY`(`parser_input`, `parser_output.items`), `UNRECOGNIZED_STREAK`(`planner_output.planner_record_update.unrecognized_streak`), `TURN_FAILED_STEPS`·`STAFF_ALERT_DELIVERY_FAILED`(`log_entry`), `INTERVENTION_FAILED`(`orchestrator_output`). 앞의 세 규칙이 정규화·판정·Plan 쪽 v1 필드에 직접 묶여 있다. 나머지는 구조·전달 필드다.
- 확인하지 못한 것: v2 TurnTrace의 키. `survey-service/schema`의 `CONTRACT_VERSION`은 아직 1이고 TurnTrace도 v1 형태라 v2 trace 키를 읽을 수 없었다. 그래서 규칙과 비식별 사본은 손대지 않았다(survey-service 이전 뒤 변경 필요).

시험(당시): `.venv/bin/python -m pytest -q` 133 통과, 0 실패. `scripts/smoke.sh` SMOKE OK (v2.0.0 wheel, 이 WSL 한 환경에서만 확인).

## 2026-10-27 현재 상태 (main, 0b487a3 기준)

- 시험: `.venv/bin/python -m pytest --collect-only -q`로 195건 수집, `.venv/bin/python -m pytest -q`로 `195 passed, 4 warnings`(이 WSL 한 환경, 2026-10-27에 확인). 위의 85건·133건은 각 시점의 이력이다. `scripts/smoke.sh`는 이번에 돌리지 않았다(미확인).
- 막힌 것: 이 영역 코드 안에서 막힌 것은 없다. 그러나 "막힌 것 없음"은 아니다. 다른 영역 의존·사용자 결정으로 열려 있는 항목은 README.md 5절(G14, G11, D-A, D-E)과 위 "남은 일"에 있다.
- G11(문진 스키마 정수 버전 1 안의 비호환 변경, D79): ops-feedback은 사본 `_schemas/survey/v1`을 따라갔고(CHANGELOG 0.9.0), 배포는 survey-service와 같은 잠금으로 함께 해야 한다. 이 항목의 닫힘 여부는 `head/docs/OPEN_GAPS.md`가 정하며 이번 세션에서는 읽지 않았다(미확인). 공동 배포 쪽이 끝나기 전에는 닫힌 것으로 보지 않는다.
- 대장 번호 없음: O11(분류체계), O16(허용 중단 시간·복구 시점), 비식별 기준은 이 영역 문서에만 나오는 이름이고 OPEN_GAPS의 ID와 대응하는 것이 확인되지 않았다(이름의 출처는 미확인).

## 2026-10-28 장애 대응(FAULT_MATRIX D1~D6, A6 서버 쪽) 반영

한 일은 CHANGELOG 0.11.2(PostgreSQL 대기 상한)와 0.11.3(이웃·중복·중단). 칸별 현재 동작은 docs/boundary.md "장애 칸별 현황".
- 시험(이 WSL 한 환경, 호스트 Docker PostgreSQL 16, `TEST_DATABASE_URL`+`TEST_PG_CONTAINER` 지정): `pytest -q` 405 통과 0 건너뜀. PostgreSQL 없이는 295 통과 110 건너뜀(같은 405건). `scripts/smoke.sh` SMOKE OK(SQLite 구성).
- 남은 일: 컨테이너 조합에서의 재측정(deploy E2E 5p, 헤드). 롤백 기준 값(사용자 결정)과 운영 설정의 `gate_service` 연결(P1-3)은 그대로다: 이 영역이 값을 채우지 않았고 `auto_submit` 예시는 false다.
- 막힌 것: 이 영역 코드 안에서는 없음. 아래 "알려진 위험"은 사용자·헤드 판단이 필요하다.
- 알려진 위험(고치지 않음): (1) I4가 일시적으로 응답하지 않을 때 만든 사례는 `question_set` 없이 게시되고 version 1은 다시 쓰지 않는다(기존 시험이 이 동작을 정한다). (2) `retention.raw_days`를 정하면 보존 삭제가 처리 표시와 무관하게 오래된 원본을 지우므로 잡이 `raw_days`보다 오래 멈추면 미처리 원본도 사라진다(현재 null). 

## 2026-10-28 전수 점검 반영 (0.12.0)

한 일은 CHANGELOG 0.12.0, 결정 이유는 docs/adr/. 점검 지적별 처리는 헤드 보고서 표 참조.
- 안 고침: C5 암호화(deploy), C6 열람 기록 개인 ID(계약), C7 백업(O16/deploy), C9 스키마 버전 표, D3 같은 키 다른 내용 신호(계약), B2/B3 토큰 역할·회전·길이, F2 이미지 해시 고정, S5 위조 Trace(CR-006으로 헤드에 넘김).
- 독립 재점검 round2(0.12.1): 이메일 패턴 제곱 시간, 보존 불리언 엄격, 재시도 대상 원본 보호, delete-session 0건 신호, readyz 경고를 고쳤다. deploy 설정의 이메일 잔존 패턴 길이 제한은 deploy에 요청한다.
- 위험: 확정 사례도 180일 뒤 지워진다(보호하려면 `cases_purge_confirmed: false`).

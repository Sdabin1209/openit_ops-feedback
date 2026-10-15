# ops-feedback 경계 문서

상대 영역은 블랙박스다. 아래는 `../boundary-contracts`(INTERFACES.md, CONVENTIONS.md)와 이 영역의 구현이 만나는 지점이다.

## 소유하는 저장소 (쓰기는 이 영역만)

| 저장소 | 내용 | 읽는 쪽 |
| --- | --- | --- |
| 로그 저장소 `log.db` | 수집한 `TraceEnvelope` 원본, 처리 표시 | 비식별 처리기(`Deidentifier`)만 |
| 실패 사례 저장소 `failures.db` | `FailureCase`(비식별 뒤), 비식별 trace, 지표 스냅샷, 롤백 요청·영수증 | I7 SDK로 이웃 영역, 검토 큐, 대시보드 |

## 제공

| 인터페이스 | 구현 | 메모리 구현 | 비고 |
| --- | --- | --- | --- |
| I6 TraceIngest `send` | `ops_feedback.logstore.LogStore.sqlite(path)`, HTTP 서비스 `ops_feedback.api`(ingest-api), 클라이언트 `boundary_contracts.clients.HttpTraceIngest` | `LogStore.memory()` | `(session_id, turn_id)` 중복 제거, 먼저 받은 것 유지, DUPLICATE |
| I7 FailureStore | `ops_feedback.failurestore.FailureStoreImpl.sqlite(path)` | `FailureStoreImpl.memory()` | `publish_case`/`get_case`/`list_cases`/`get_trace` |

두 구현은 같은 클래스와 로직 위에 백엔드만 다르고, 같은 적합성 시험을 통과한다(`tests/test_conformance_ingest.py`, `tests/test_conformance_failurestore.py`).

I7 상세(구현이 정한 것):
- `publish_case`: 스키마 검증 -> `(case_id, version)` 같은 내용이면 기존 것 반환, 다르면 `CONFLICT`. version은 1부터 연속(건너뛰면 `FAILED_PRECONDITION`). `deidentified_trace_ref`의 trace가 먼저 있어야 한다(`FAILED_PRECONDITION`).
- `list_cases`: 정렬은 게시 순서(append), `next_cursor`는 불투명 문자열. `latest_only=True`면 사례별 최신 version만. `updated_since`는 version의 `created_at` 기준.
- HTTP(HTTP.md): `GET /v1/cases/{case_id}?version=`, `GET /v1/cases`(필터 + `{items, next_cursor}`, `limit` 기본 100 최대 500), `GET /v1/deidentified-traces/{ref}`. 읽기 토큰은 `OPS_READ_TOKENS`의 역할별 값이고 수집 토큰과 분리된다(서로의 경로 403). 응답은 `FailureCase` 스키마 검증을 거친다. `publish_case`는 열지 않는다.
- `get_trace`: 비식별 trace. 결과가 `TurnTrace` 스키마를 통과하지 않을 수 있다(CR-001).
- 소비자: `review_status == CONFIRMED`이고 `expected`가 있는 것만 정답 있는 회귀 케이스로 쓴다(I7 규정).

## 소비하는 계약과 지원 버전

| 계약 | 용도 | 지원 |
| --- | --- | --- |
| `TraceEnvelope` | I6 입력 | 경계 계약 `pipeline_schema_version` `1.x`(스냅샷 기준). 스키마가 `additionalProperties: false`이므로 MINOR 추가는 스냅샷 갱신 후 반영 |
| `TurnTrace`(문진 스키마) | 수집 검증, 비식별의 걷기 기준 | 정수 버전 **1**. 폴더 `src/ops_feedback/_schemas/survey/v<정수>/`를 추가하면 여러 버전을 동시에 읽는다. 목록에 없는 `runtime_contract_version`은 `UNSUPPORTED_VERSION` |
| `IngestResult`, `FailureCase`, `RollbackRequest`, `RollbackReceipt`, `ActivePointer` | 쓰기 전 검증 | `1.x` |
| `ReleaseManifest`, `GateDecision` | I4 SDK로만 읽음(현재 코드는 `release_id`만 사용) | `1.x` |
| I4 ReleaseReader | 활성 포인터, 릴리스 목록 | `boundary_contracts.sdk.ReleaseReader` Protocol |
| I5 `request_rollback` | 롤백 요청 전달 | `boundary_contracts.sdk.GateService` Protocol |

경계 계약 스키마와 코드는 `boundary-contracts` 패키지(태그 v1.2.0, `boundary_contracts.schema_dir()`)에서 읽는다. 문진 스키마만 `scripts/sync_vendor.sh`로 만든 스냅샷(`_schemas/survey/v<정수>/`)을 쓰고 `tests/test_schema_sync.py`가 원본과의 일치를 확인한다(survey-service가 패키지로 내면 교체). 이미지는 `scripts/build_wheels.sh`가 `git archive v1.2.0`으로 만든 wheel을 설치한다.

## 보장하는 것

- 중복 전달은 Trace 중복을 만들지 않고 결과가 같다(DUPLICATE).
- `FailureCase`에는 비식별을 마친 것만 들어간다. 비식별이 실패하면(`DeidError`) 사례를 만들지 않는다.
- 모든 사례에 `source_release_id`(봉투의 `release_id`).
- 같은 Trace를 두 번 처리해도 같은 사례(case_id, trace ref, created_at이 입력에서 결정됨).
- 롤백 요청에는 현재·되돌릴 릴리스 ID와 근거 지표 ID(`evidence_refs`)가 있다. 요청만 하고 포인터는 바꾸지 않는다.
- 경계 호출의 실패는 `BoundaryError`(UNAVAILABLE만 재시도 가능).

## 장애 영향

| 장애 | 영향 | 대응 |
| --- | --- | --- |
| ingest-api 중단 | 새 Trace 수집이 멈춘다. 문진 서비스의 턴은 멈추지 않는다(서비스가 버퍼링하고 `UNAVAILABLE`일 때 재시도, I6 합의) | 재시작 후 서비스가 버퍼를 비운다. 중복은 DUPLICATE로 흡수 |
| `log.db` 쓰기 불가 | `send`가 `UNAVAILABLE` | 디스크·권한 확인 |
| 잡 중단·지연 | 실패 사례 게시가 늦어진다. 원본은 안전하다. 롤백 평가도 늦어져 나쁜 릴리스 감지가 지연된다 | 다음 실행이 처리 표시 기준으로 이어서 처리 |
| 비식별 실패 | 해당 Trace는 사례가 되지 않고 `DEID_FAILED`로 남는다(잡 종료 코드 1) | 정책 보정 후 `--retry-deid-failed` |
| 게이트 `UNAVAILABLE` | 롤백 요청이 전달되지 않는다. 요청은 저장되어 같은 id로 재전송된다 | 다음 실행에서 자동 재시도 |
| `failures.db` 장애 | I7 읽기 소비자(합성 데이터, model-lab, 문항 저작)가 새 사례를 못 받는다. 수집은 영향 없음 | 복구 후 잡이 이어서 게시 |

허용 중단 시간·복구 시점(O16)은 사용자 결정이다. 백업·복제는 정해지지 않았다.

## 건드리지 않는 것

- 롤백 수행과 활성 포인터(게이트 소유).
- 다른 영역의 저장소와 코드. 이웃의 SDK는 Protocol로만 받는다.
- 경계 계약과 문진 스키마(변경은 `docs/change-requests/` 5건 참조).

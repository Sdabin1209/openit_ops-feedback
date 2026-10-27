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
| I6 TraceIngest `send` | `ops_feedback.logstore.LogStore.postgres(url)`(운영) 또는 `.sqlite(path)`(개발·시험), HTTP 서비스 `ops_feedback.api`(ingest-api), 클라이언트 `boundary_contracts.clients.HttpTraceIngest` | `LogStore.memory()` | `(session_id, turn_id)` 중복 제거, 먼저 받은 것 유지, DUPLICATE |
| I7 FailureStore | `ops_feedback.failurestore.FailureStoreImpl.postgres(url)`(운영) 또는 `.sqlite(path)` | `FailureStoreImpl.memory()` | `publish_case`/`get_case`/`list_cases`/`get_trace` |

두 구현은 같은 클래스와 로직 위에 백엔드만 다르고, 같은 적합성 시험을 통과한다(`tests/test_conformance_ingest.py`, `tests/test_conformance_failurestore.py`).

I7 상세(구현이 정한 것):
- `publish_case`: 스키마 검증 -> `(case_id, version)` 같은 내용이면 기존 것 반환, 다르면 `CONFLICT`. version은 1부터 연속(건너뛰면 `FAILED_PRECONDITION`). `deidentified_trace_ref`의 trace가 먼저 있어야 한다(`FAILED_PRECONDITION`).
- `list_cases`: 정렬은 게시 순서(append), `next_cursor`는 불투명 문자열. `latest_only=True`면 사례별 최신 version만. `updated_since`는 version의 `created_at` 기준.
- HTTP(HTTP.md): `GET /v1/cases/{case_id}?version=`, `GET /v1/cases`(필터 + `{items, next_cursor}`, `limit` 기본 100 최대 500), `GET /v1/deidentified-traces/{ref}`. 읽기 토큰은 `OPS_READ_TOKENS`의 역할별 값이고 수집 토큰과 분리된다(서로의 경로 403). 응답은 `FailureCase` 스키마 검증을 거친다. `publish_case`는 열지 않는다.
- 검토 게시(Protocol 밖, HTTP.md): `POST /v1/cases/{case_id}/review` 쓰기 역할 `reviewer`(`OPS_REVIEW_TOKENS`). `ReviewQueue`를 재사용해 version을 올린 FailureCase(201, 재전송 200, 없으면 404)를 게시하고 스키마로 검증한다. `reviewed_input`(선택, 검토자가 개인정보 없이 직접 쓴 입력)은 `expected_kind`에 맞는 ActionExtractionInput(LORA_A)|ResponseRequest(LORA_B)이어야 하고 CONFIRMED에만 붙는다. `expected`는 LORA_A면 `ActionExtraction`(객체), LORA_B면 `ResponseText`(문자열)이어야 한다(검토자가 쓴 값 자체의 개인정보 여부는 저장소가 판별하지 못한다. 검토 화면은 비식별 trace만 보여 줘야 한다).
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

경계 계약 스키마와 코드는 `boundary-contracts` 패키지(태그 v2.0.2, `boundary_contracts.schema_dir()`)에서 읽는다. 문진 스키마만 `scripts/sync_vendor.sh`로 만든 스냅샷(`_schemas/survey/v<정수>/`)을 쓰고 `tests/test_schema_sync.py`가 원본과의 일치를 확인한다(survey-service가 패키지로 내면 교체). 이미지는 `scripts/build_wheels.sh`가 `git archive v2.0.2`으로 만든 wheel을 설치한다.

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
| PostgreSQL 무응답(TCP는 받되 응답 없음, `docker pause` 같은 얼음) | 아래 "DB 대기 상한" 절. 요청은 상한 안에 `UNAVAILABLE`(HTTP 503)로 끝나고(호출자는 재시도), `/readyz`는 2초 안에 503, 잡은 종료 코드 2 | 서버가 되살아나면 서비스 재시작 없이 다음 호출이 새 연결로 복구 |
| 수집 토큰 불일치(survey-service 설정·회전 오류) | `POST /v1/traces`가 403 `PERMISSION_DENIED`. 서버 로그에 `거절 POST /v1/traces -> PERMISSION_DENIED` 경고가 남는다(토큰 값 없음). `/readyz`에는 인증 실패 신호가 **없다**(건수 지표를 만들지 않았다) | 로그의 경고를 감시한다. 호출자 쪽 DEAD 처리는 survey-service 영역의 일 |
| `failures.db` 장애 | I7 읽기 소비자(합성 데이터, model-lab, 문항 저작)가 새 사례를 못 받는다. 수집은 영향 없음 | 복구 후 잡이 이어서 게시 |

### DB 대기 상한 (PostgreSQL, FAULT_MATRIX P0-3 D1·D4)

환경 변수로 바꾼다(비밀이 아니므로 값을 그대로 준다. 서버와 잡이 같다). SQLite 경로에는 적용되지 않는다(기존 `busy_timeout` 그대로).

| 변수 | 기본(초) | 뜻과 근거 |
| --- | --- | --- |
| `OPS_DB_CONNECT_TIMEOUT` | 3 | 접속 상한(libpq는 정수 초로 올림). 이전에는 10초 고정 |
| `OPS_DB_STATEMENT_TIMEOUT` | 3 | 문장 하나의 상한. 서버 쪽 `statement_timeout`으로 걸고, 서버가 얼어 그 설정을 듣지 못하면 +1초 뒤 클라이언트가 소켓을 닫아 끝낸다 |
| `OPS_DB_PROBE_TIMEOUT` | 2 | `/readyz`의 DB 확인 전체(접속+문장) 상한 |
| `OPS_DB_DDL_TIMEOUT` | 30 | 시작 때 스키마 생성 한정. 다른 인스턴스가 쥔 advisory lock을 기다릴 수 있어 길다 |

기본값의 근거: 이 영역의 문장은 인덱스 있는 단건 INSERT/SELECT와 잡의 한 배치 조회라 정상일 때 수 ms~수십 ms다(측정한 값이 아니라 문장 종류에서 본 추정). 3초면 느린 디스크나 잠깐의 잠금은 견디고, 수집 호출자가 `UNAVAILABLE`을 재시도하므로 오래 매달리는 것보다 빨리 실패하는 편이 안전하다. 0 이하·숫자 아님은 시작 때 거부한다.

- `/readyz`: 주 연결과 그 락을 쓰지 않고 요청마다 새 연결로 `SELECT 1`을 한다(두 저장소는 동시에). 요청 경로가 주 연결에서 기다리는 중이어도 막히지 않는다. 실패하면 503 `{"status":"not-ready","code":"UNAVAILABLE"}`, 복구되면 다음 호출부터 200.
- 요청 경로: 시도 하나가 `STATEMENT+1`초 안에 끝난다. 연결이 끊긴 것으로 보이면 재접속(`CONNECT` 상한)으로 한 번 재시도하므로 기본값의 최악은 약 7초(4+3)다. 이어서 `UNAVAILABLE`.
- 잡: DB가 무응답이면 같은 상한으로 `UNAVAILABLE`이 되고 잡은 종료 코드 **2**(스택 트레이스 없이 stderr에 JSON 한 줄). 비식별 실패 등 처리 오류의 종료 코드 1과 구분된다.
- 측정(이 세션, 호스트의 Docker PostgreSQL 16 컨테이너를 `docker pause`, 기본값): `/readyz` 503까지 2.0초, 수집 POST 503까지 7.0초, 잡 종료 코드 2까지 3.3초. 같은 시험을 수정 전 코드로 돌리면 `/readyz`가 15초 안에 응답하지 못했다(클라이언트 포기, 상한 없음), 잡은 접속 10초 뒤 스택 트레이스와 종료 코드 1. 컨테이너 조합에서의 재측정은 deploy E2E 5p에서 헤드가 한다.

### 보존·삭제·개인정보 위치 (점검 C1·C3, D88/U4)

| 저장 위치 | 내용 | 개인정보 | 보존 |
| --- | --- | --- | --- |
| `raw_traces.envelope`(원본 DB) | TraceEnvelope 전체(발화·답변·직원 ID 포함) | 있음, 평문 | 처리 끝난 것 30일(`retention.raw_days`), 처리 전인 것은 지우지 않음. `raw_after_processing: minimize`면 처리 직후 내용만 삭제 |
| `raw_traces`의 키·해시·시각·릴리스·처리 표시 | 중복 판별과 집계 | session_id·turn_id가 원본 값 그대로 | 위와 같은 행 수명(`purge`가 행째 지움) |
| `cases.record`, `traces`(사례 DB) | 비식별 뒤 FailureCase·trace | 비식별(ADR 0004). session_id·turn_id는 salt 해시 | 사례 180일(`cases_days`), 고아 trace 함께 |
| `aux`(지표·롤백 요청·영수증) | 집계, 릴리스 ID | 없음 | 365일(`metrics_days`) |
| 서버 로그 | 요청 줄(메서드·경로), 거절 경고 | 없음(경로에 ID가 들어갈 수 있으나 이 서비스 경로는 case_id/ref) | 로그 보존은 deploy |

**기본값 주의 (두 경로가 다르다)**: 잡의 자동 정리는 `retention.cases_purge_confirmed` 기본 **true**라 확정(CONFIRMED) 사례도 180일 뒤 지운다. 수동 `admin purge --cases-days`는 반대로 **확정 사례를 지우지 않고** `--include-confirmed`를 줘야 지운다. 이 차이는 사용자 결정(round2 L12) 전의 현재 기본값이며, 둘 다 불리언 설정/명시 플래그다(문자열 `"false"`는 시작 거부). 처리 기한(`raw_days`) 삭제는 재시도 대상(DEID_FAILED, PUBLISH_FAILED) 원본을 지우지 않고 별도 기한 `failed_raw_days`를 명시했을 때만 그 값을 쓰고, 적지 않으면 `raw_days`를 따른다(기본값 없음, raw_days가 forever면 forever). admin purge의 고아 trace 정리는 잡과 겹치면 방금 저장한 trace를 지울 수 있어, 잡은 그 경우 한 번 다시 저장·재시도한다(그래도 가능하면 같은 시각에 돌리지 않는다).

삭제 경로: 잡의 자동 정리, `python -m ops_feedback.admin`(세션 단위 삭제 포함, 비밀 소유가 인증이며 서버 API로는 열지 않았다). 확인하지 못한 것: 디스크·백업의 암호화와 백업 보존(deploy), 운영 잡 설정의 실제 값. 운영 잡 설정(`deploy` 소유)에 `retention: {raw_days: 30, cases_days: 180, metrics_days: 365}`를 두고 서버에 `OPS_RAW_RETENTION_DAYS=30`을 선언하도록 deploy에 요청한다(이 영역은 deploy 파일을 수정하지 않는다).

### 장애 칸별 현황 (FAULT_MATRIX D2·D3·D5·D6·A6 서버 쪽)

"시험"은 이 세션에서 돌려 통과한 것(가짜 상류는 실제 HTTP 서버와 `boundary_contracts.clients`의 실제 클라이언트, 클라이언트 timeout 1초로 줄임)이다. 이웃 영역의 실제 서비스와는 연결해 보지 않았다.

| 칸 | 현재 동작 | 근거 |
| --- | --- | --- |
| D2 I4 읽기: 멈춤·5xx·깨진 JSON | 클라이언트 timeout(SDK 기본 10초, 호출 한 번의 대기) 뒤 UNAVAILABLE. 롤백 평가 전체는 `rollback.deadline_seconds`(기본 60초)가 막는다. 사례 게시는 이미 끝나 있고 보고서 `rollback.action=ERROR`, `errors`에 기록, 종료 코드 1. 롤백 요청은 만들지 않는다 | 시험 |
| D2 I4 읽기: 401/403, 형식 이상 | PERMISSION_DENIED / INTEGRITY_ERROR로 같은 처리(재시도 없이 즉시 실패, 보고서·종료 코드에 보임) | 시험 |
| D2 I4 `get_release`(사례의 `question_set`) | 읽지 못하면 필드를 생략해 게시한다(1.0 호환). UNAVAILABLE이면 그 실행에서는 더 부르지 않는다. **알려진 위험**: 일시 장애 때 만든 사례는 `question_set` 없이 남는다(version 1은 다시 쓰지 않음). 고치지 않았다(기존 시험이 정한 동작) | 시험(한 실행 한 번 호출), 위험은 코드 읽음 |
| D3 I5 요청: 멈춤·5xx·깨진 JSON | 요청은 먼저 저장되고(`rollback_request`) 전달이 실패하면 영수증이 없다. 보고서 `submit_error`와 `errors`(종료 코드 1). 다음 실행이 **같은 request_id**로 다시 보낸다 | 시험 |
| D3 I5: 401/403, 형식 이상, 다른 request_id의 영수증 | 재시도로 해결되지 않는 오류를 `errors`에 드러낸다(조용히 버리지 않는다). 잘못된 영수증은 저장하지 않는다 | 시험 |
| D3 중복 | request_id는 (현재 릴리스, 되돌릴 릴리스, 포인터 버전)에서만 정해져 재실행·동시 실행이 같은 요청을 가리킨다. 영수증이 있으면 다시 보내지 않는다. 지표 스냅샷도 요청을 처음 만들 때 한 번만 쓴다 | 시험 |
| D3 처리됐으나 응답을 잃은 경우(멈춤) | 다음 실행이 같은 request_id를 다시 보낸다. 중복 처리 방지는 **게이트가 request_id를 멱등으로 받는 것에 달려 있다**. 게이트 쪽 동작은 확인하지 못했다 | 이 영역 쪽만 시험 |
| D3 `auto_submit` | 설정 예시는 false(주석), 코드에 기본값이 없고 `rollback` 항목을 두면 반드시 적어야 한다. false이면 게이트를 부르지 않고 요청만 저장한다. 롤백 기준 값과 운영 설정의 `gate_service` 연결은 채우지 않았다(사용자 결정, P1-3) | 시험(false), 설정 코드 읽음 |
| D5 I7 읽기: DB 막힘·느림 | 위 "DB 대기 상한": 문장 상한 안에 503(UNAVAILABLE). GET의 예상 밖 예외도 503 본문. 호출자(question-authoring)의 클라이언트 timeout은 그 영역 일 | 시험(`docker pause`) |
| D6 검토 게시 중 장애 | 게시는 단일 INSERT라 반쯤 쓴 상태가 없다. DB 무응답이면 상한 안에 503, 같은 본문을 다시 보내면 한 번만 반영된다(201 뒤 200, 새 version 없음). 서버와 잡·검토자 둘이 같은 version을 동시에 게시하면 먼저 쓴 쪽이 남고 진 쪽은 CONFLICT(이전에는 성공으로 응답할 수 있었다) | 시험 |
| 잡이 도중에 죽음 | 사례 하나는 trace 저장, 게시, 처리 표시 순서다. 게시 직후·게시 전에 죽어도 다시 실행하면 이중 게시 없이 이어지고 모든 사례가 비식별 trace를 가리킨다 | 시험(예외로 중단 모사. 프로세스 강제 종료 자체는 시험하지 않음) |
| A6 수집 중복·재전송 | 같은 (session_id, turn_id)는 동시에 8개를 보내도 한 건만 ACCEPTED, 나머지 DUPLICATE. 같은 키에 다른 내용이 와도 먼저 받은 것이 남는다(덮어쓰지 않음, 알려 주는 오류도 없음). 잘못된 봉투는 아무것도 쓰지 않는다. DB 장애로 실패한 전송을 재전송하면 ACCEPTED 한 번 뒤 DUPLICATE | 시험 |
| A6 F7 인증 실패 | `POST /v1/traces`가 403. 서버 로그에 경고(경로·코드만). 호출자 쪽 DEAD 건수의 노출은 survey-service 영역이고 이 영역 `/readyz`에는 건수가 없다 | 시험(로그) |

종료 코드: 0 정상, 1 처리 오류 있음(비식별 실패, I4/I5 오류 포함. 어느 단계인지는 보고서 `errors`), 2 시작 못 함(DB 무응답, 설정 오류). 확인하지 못한 것: 프로세스 강제 종료(SIGKILL) 뒤 재실행, 게이트의 request_id 멱등, 컨테이너 조합(deploy E2E)에서의 재측정(헤드).

허용 중단 시간·복구 시점(O16)은 사용자 결정이다. 백업·복제는 정해지지 않았다.

## 건드리지 않는 것

- 롤백 수행과 활성 포인터(게이트 소유).
- 다른 영역의 저장소와 코드. 이웃의 SDK는 Protocol로만 받는다.
- 경계 계약과 문진 스키마(변경은 `docs/change-requests/` 5건 참조).

## 비밀 전달 (D84)

토큰·salt는 `boundary_contracts.read_secret(NAME)`으로 읽는다: `NAME_FILE`(파일 경로)이 우선이고 없으면 `NAME` 환경 변수(개발용). 대상은 `OPS_INGEST_TOKEN`, `OPS_READ_TOKENS`, `OPS_REVIEW_TOKENS`, `OPS_DEID_SALT`, 잡 설정 `{env: 이름}`의 토큰(`OPS_GATE_READ_TOKEN`, `OPS_GATE_WRITE_TOKEN` 등)이다. 서버와 잡이 같다. 상세는 runbook.md의 "비밀은 NAME_FILE로".
잡 설정에 비밀 값을 직접 적으면 시작을 거부하고, 비밀은 `{secret: NAME}`/`{env: NAME}`으로만 참조한다(runbook.md).

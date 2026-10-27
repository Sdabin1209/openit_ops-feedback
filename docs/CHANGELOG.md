# 변경 이력

## 0.12.0 (제안, 전수 점검 반영 + D88 + D89)

- 비식별(C4/M-3): `keep_string_keys`의 값은 키별 전체 일치 패턴(`deid.DEFAULT_KEEP_PATTERNS`, 설정 `deid.keep_string_patterns`로 변경)에 맞을 때만 남고 아니면 `[MASKED]`. 패턴 없는 keep 키는 잡 보고서 경고. 검토자가 쓰는 `expected`·`reviewed_input`·`note`에 이메일·전화·주민번호 패턴이 있으면 INVALID_RECORD로 거부(M-4).
- 보존(C3, D88/U4): `retention.raw_days` 30, `cases_days` 180, `metrics_days` 365가 기본. null·0·문자열은 시작 거부, `"forever"`는 경고와 함께 허용. 처리 표시 없는 원본은 지우지 않고 기한을 넘기면 보고서 경보(`UNPROCESSED_RAW_PAST_RETENTION`). `raw_after_processing: minimize`로 처리가 끝난 봉투 내용을 지움(중복 판별 키 유지). 잡이 사례·고아 trace·지표/롤백 기록도 기한 뒤 지운다.
- 삭제 도구 `python -m ops_feedback.admin`: `delete-session`(원본·비식별 trace·사례), `purge`, `minimize-raw`, `migrate`. 서버 API로는 열지 않았다(비밀 소유가 인증).
- 서버(A9/B10/A4/B4/B5/E2): 동시 연결 상한(`OPS_MAX_CONNECTIONS` 64)과 소켓 timeout 30초, `/readyz` 결과 1.5초 공유(`OPS_READYZ_CACHE_SECONDS`)와 카운터(`auth_rejected` 등)·보관 경고, SIGTERM 정상 종료, JSON 로그 이스케이프, 깊은 JSON·비ASCII 토큰 헤더 처리.
- 잡(G3): 미처리 원본을 `job.batch_size`(500)씩 seq 순으로 읽는다.
- 검토 게시(U7): `X-Openit-User`가 없으면 거부가 기본(`OPS_REVIEW_REQUIRE_USER=0`으로 끔). 있으면 `reviewed_by`에 기록, 토큰 역할은 변함없이 기본값.
- 설정(U11/S5): `rollback.auto_submit`은 불리언만(문자열 거부). 롤백 기준 값은 채우지 않음. 위조 Trace 위험은 CR-006.
- DDL 분리(B12/C8): `OPS_DB_SCHEMA=verify`이면 앱이 DDL을 실행하지 않고 표 존재만 확인(DDL 권한 없는 계정으로 시험). 스키마는 `admin migrate`.
- 시험 실행(H3): `scripts/dev-postgres.sh`, `make test-pg`.
- ADR 10건(`docs/adr/`, D89).

## 0.11.3 (제안, FAULT_MATRIX D2·D3·D5·D6·A6)

- 롤백 평가(잡): I4 읽기·I5 전달 실패를 잡아 보고서 `rollback`에 `{"action":"ERROR","error":코드}`로 남기고 `errors`에 넣는다(종료 코드 1). 이전에는 I4 실패가 잡 전체를 예외로 끝내(이미 게시한 사례의 보고서 없이) 종료 코드 2였고, I5 `submit_error`(401/403 포함)는 보고서에만 있고 종료 코드 0이었다. 평가 전체에 상한(`rollback.deadline_seconds`, 기본 60초, 기준 값이 아님)을 둔다.
- I5 영수증의 `request_id`가 요청과 다르면 저장하지 않는다(INTEGRITY_ERROR). 형식이 다른 응답은 SDK가 INTEGRITY_ERROR로 막는다.
- 지표 스냅샷은 요청을 처음 만들 때만 쓴다(이전에는 실행마다 `window_end`가 달라 같은 상황에서도 새 행이 쌓였다). 요청 id는 변함없이 (현재, 되돌릴, 포인터 버전)에서 정해진다.
- 잡: I4가 UNAVAILABLE이면 같은 실행에서 다른 릴리스를 다시 부르지 않는다(릴리스마다 timeout을 기다리지 않게).
- `publish_case`: 쓴 뒤 저장된 내용을 다시 읽어 대조한다. 두 프로세스가 같은 (case_id, version)을 동시에 게시하면 먼저 쓴 쪽이 남고 진 쪽은 CONFLICT를 받는다(이전에는 PostgreSQL에서 진 쪽도 성공으로 응답했고 SQLite에서는 UNAVAILABLE이었다).
- 읽기 API(GET): 예상 밖 예외도 연결을 끊지 않고 503 본문으로 낸다.
- 시험: `tests/test_upstream_faults.py`(실제 HTTP 가짜 상류: 멈춤·5xx·깨진 JSON·401/403·형식 이상·다른 request_id, 잡 중단, 중복 수집, 게시 경합), `tests/test_postgres_timeout.py`에 읽기·검토 게시·재전송 시험 추가.

## 0.11.2 (제안, FAULT_MATRIX P0-3 D1·D4)

- PostgreSQL 대기 상한: 접속(`OPS_DB_CONNECT_TIMEOUT`, 기본 3초, 이전 10초 고정), 문장(`OPS_DB_STATEMENT_TIMEOUT`, 3초, 서버 쪽 `statement_timeout` + 서버가 얼면 +1초 뒤 클라이언트가 소켓을 닫음), 스키마 생성(`OPS_DB_DDL_TIMEOUT`, 30초). 이전에는 문장 상한이 없어 서버가 응답하지 않으면 무한 대기할 수 있었다.
- `/readyz`는 주 연결·락을 쓰지 않고 새 연결로 `SELECT 1`을 한다(`OPS_DB_PROBE_TIMEOUT`, 기본 2초, 두 저장소 동시 확인). DB 무응답이면 2초 안에 503, 복구되면 200.
- 잡: DB 무응답이면 `UNAVAILABLE`을 잡아 종료 코드 2로 끝낸다(스택 트레이스 없음). 처리 오류의 종료 코드 1과 구분.
- 수집 서버: 인증 실패(`PERMISSION_DENIED`)와 `UNAVAILABLE` 응답을 경고 로그로 남긴다(경로와 코드만, 토큰 없음). FAULT_MATRIX A6 F7. `/readyz`에 인증 실패 건수는 넣지 않았다.
- 시험: `tests/test_postgres_timeout.py`(docker pause, `TEST_DATABASE_URL`+`TEST_PG_CONTAINER`가 없으면 SKIP), `tests/test_api.py` 인증 거절 로그. SQLite 경로 변경 없음.

## 0.11.1 (제안)

- PostgreSQL 연결이 끊기면(DB 서버 재시작, 연결 강제 종료) 연결을 다시 열고 같은 호출을 한 번 재시도한다. 연결 끊김 오류만 대상이고 SQL 오류는 재시도하지 않는다. 재접속도 실패하면 UNAVAILABLE(오류에 접속 정보 없음). 서비스 재시작이 필요 없다.
- 불확실한 쓰기(끊긴 순간 서버가 커밋했을 수 있음)는 멱등이라 재시도해도 데이터가 같다(INSERT는 ON CONFLICT). 달라질 수 있는 것은 반환 건수뿐이다(예: 응답을 못 받은 첫 수신이 재시도에서 DUPLICATE). `insert_case`도 재시도 안전하게 바꿨다. `tests/test_postgres_reconnect.py` 추가.

## 0.11.0 (제안)

- 운영 저장소로 PostgreSQL 추가(`psycopg[binary]==3.3.6` 고정). `OPS_LOG_DATABASE_URL`, `OPS_FAILURE_DATABASE_URL`(`read_secret`, `_FILE` 우선)이 있으면 PostgreSQL, 없으면 기존 SQLite. 서버와 잡 모두 `storage.py`의 같은 선택 함수를 쓴다. `/readyz`에 저장소 종류만 표시.
- 적합성 시험을 memory·sqlite·postgres로 매개변수화(`TEST_DATABASE_URL`이 없으면 postgres는 SKIP). `tests/test_conformance_rawmeta.py`(처리 표시·집계·보존 삭제), `tests/test_postgres_select.py`(선택, 접속 문자열 비노출, 서버·잡) 추가.

## 0.10.0 (제안, boundary-contracts v2.0.2, D84)

- 모든 토큰·salt를 `boundary_contracts.read_secret`으로 읽는다(`NAME_FILE` 우선, 없으면 `NAME`). 서버(`api.py`)와 잡(`job.py`, 설정의 `{env: 이름}` 인자 포함). `docker exec env`/`inspect`에 평문이 남지 않는다.
- boundary-contracts를 v2.0.2로 올렸다. `tests/test_secrets.py` 추가(비밀이 출력·오류 본문·DB에 없음). 스모크는 비밀을 파일로 주고 inspect·exec env에 값이 없는지 검사한다.

## 0.10.1 (제안, D84)

- 잡 설정에서 비밀처럼 보이는 키(token, secret, password, credential, api_key, authorization, private_key)에 문자열 값이 직접 있으면 `load_config`가 거부한다(키 경로만 오류에 싣는다). 설정의 비밀 참조는 `{secret: 이름}` 또는 `{env: 이름}`이며 중첩 dict·list에서도 풀린다. `*_env` 이름 키는 호환 유지.

## 0.9.1 (제안, boundary-contracts v2.0.1)

- boundary-contracts를 v2.0.1로 올렸다(requirements, `build_wheels.sh` 기본 태그, `sync_vendor.sh` 주석, boundary.md, runbook.md). v2.0.1은 ActionExtraction properties 순서만 사전순으로 바꾼 PATCH이고 `examples/TraceEnvelope.json`을 v2 trace로 고쳤다. 코드 변경 없음.
- model-runtime에는 코드·요구 파일 의존이 없다(이미지 라벨 값만 빌드 인자로 받는다). 올릴 것이 없다.

## 0.9.0 (제안, 새 TurnTrace 따라가기: D79, survey-service ce480ab)

- 문진 스키마 사본 `_schemas/survey/v1` 동기화(`scripts/sync_vendor.sh`). 정수 버전은 1 유지(D79). 같은 정수 안의 비호환 변경이므로 survey-service와 같은 잠금으로 함께 배포해야 한다(OPEN_GAPS G11).
- 비식별: `mask_value_keys` 정책 추가(타입 무관 마스킹), 지목한 `mask_defs`·`drop_defs`가 스키마에 없으면 `DeidError`. 예시 설정: `drop_defs: []`(QuestionBank 없어짐), `mask_value_keys: [value]`, `keep_string_keys`에 `path`, `action`, `next_item` 추가하고 `guide_id`, `pending_id`, `category` 제거. 정책 지문이 바뀐다.
- 탐지: `GENERATOR_FALLBACK`은 `response_outcome=="fallback"`(이름 유지, 기존 `rule_to_type` 설정 호환). `PARSER_EMPTY`를 `EXTRACT_FAILED`로, `UNRECOGNIZED_STREAK`를 `REASK_STREAK`로 바꿨다. 옛 이름은 오류(대체 이름 안내). 설정을 쓰는 쪽은 `detection.detectors[].rules`와 `classification.rule_to_type`/`priority`의 규칙 ID를 바꿔야 한다.
- 시험: 새 키 잎 22개의 처분(남김/마스킹/비움)을 하나씩 확인하고 스키마 잎 목록이 바뀌면 실패하는 시험, 세션 문서 안 대화 로그 마스킹, 숫자·불리언 값 누출, 새 FailedStep 6값, 새 규칙들. 시험용 trace가 문진 스키마를 지키는지도 검증.
- FailureCase 등 경계 계약 레코드의 모양은 바뀌지 않았다.

## 0.8.0 (제안, boundary-contracts v2.0.0 이전)

- boundary-contracts v2.0.0(스키마 2.0, D74, D75)로 올렸다. requirements, wheel 태그(`build_wheels.sh`), 스모크가 확인하는 `pipeline_schema_version`을 갱신. v1 레코드는 `UNSUPPORTED_VERSION`이다.
- 검토(`ReviewQueue.review`, `POST /v1/cases/{id}/review`): `expected_kind`는 `LORA_A`|`LORA_B`. 옛 값 `PARSER`/`GENERATOR`는 400. `expected`는 LORA_A면 `ActionExtraction`(객체), LORA_B면 `ResponseText`(문자열)로, `reviewed_input`은 `ActionExtractionInput`|`ResponseRequest`로 경계 계약 스키마(`validate_record`)에서 검증한다. 문진 스키마의 `ParserOutput` 등으로 검증하던 경로와, 사례 trace에 `parser_input`/`generator_input`이 있어야 한다는 조건은 없앴다(v2 입력이 v1 TurnTrace 필드에서 나오지 않는다).
- 시험: LORA_B 문자열 정답과 `ResponseRequest` 왕복, 종류와 형태가 어긋나는 거부(객체-LORA_B, 문자열-LORA_A, v1 값, 숫자, 빈 문자열) 추가.
- 바꾸지 않은 것(survey-service가 TurnTrace를 바꿀 때까지): 비식별 처리기, 탐지 규칙, 분류. 아래 STATUS의 "정규화·판정 결합 확인" 참조.

## 0.7.0 (2026-10-15)

- 검토 POST(D71): `X-Openit-User`가 있으면 그 값을, 없으면 토큰 역할 이름을 `ext["ops-feedback"].reviewed_by`에 새 version마다 기록. Id 형식이 아니면 400.
- 멱등 기준 명확화: 최신 version과 review_status·expected·expected_kind·reviewed_input이 모두 같으면 200과 기존 레코드(reviewed_by 불변), 하나라도 다르면 201 새 version. 검토 내용은 요청이 전부 정하며 이전 version의 expected 등을 물려받지 않는다(이전에는 생략하면 물려받았고 검토자 역할도 비교에 들어갔다).
- `GET /v1/cases`가 `{items, next_cursor}`임을 시험으로 고정.

## 0.6.0 (2026-10-15)

- boundary-contracts v1.3.0(스키마 1.3, D55): 검토 POST 본문에 `reviewed_input?`를 받는다. `expected_kind`에 맞는 입력 스키마(PARSER=UtteranceParserInput, GENERATOR=ResponseGeneratorInput)로 검증하고 아니면 INVALID_RECORD. FailureCase에 담겨 I7로 나가고 재전송 멱등성 비교에 포함된다. CONFIRMED에만 붙일 수 있고, `expected` 없이 `reviewed_input`만 보내도 된다. 비식별 처리기는 이 필드를 건드리지 않는다(검토자가 개인정보 없는 문장을 직접 쓴다는 전제. 원문을 저장하는 경로는 없다).

## 0.5.0 (2026-10-15)

- 검토 게시 HTTP 면 `POST /v1/cases/{case_id}/review`(D52): `OPS_REVIEW_TOKENS`, 201/200/404/400, expected 검증(ParserOutput|GeneratorOutput + trace의 입력 존재), 세 종류 토큰 분리. `ReviewQueue`가 PENDING으로 되돌리는 검토도 받는다.
- 시험 tests/test_api_review.py, 스모크에 잡 실행 후 검토 게시·멱등·읽기 확인 추가.

## 0.4.0 (2026-10-15)

- boundary-contracts v1.2.0(스키마 1.2): requirements, wheel 태그 갱신. 새 레코드는 `PIPELINE_SCHEMA_VERSION`(1.2).
- 자체 `HttpTraceIngest`(client.py) 삭제, `boundary_contracts.clients`로 대체.
- 통합 시험(tests/test_http_clients.py): `HttpReleaseReader`를 설정 팩토리(`{env: ...}` 포함)로 만들어 작은 I4 서버에서 question_set을 읽음. 우리 서버를 `HttpTraceIngest`/`HttpFailureStore`로 호출(ACCEPTED/DUPLICATE, 읽기 토큰 수집 403, 필터·페이지, 비식별 trace).
- turn-engine·model-runtime에는 코드 의존이 없다(이미지 라벨만 기록).

## 0.3.0 (2026-10-15)

- I7 HTTP 읽기 면(HTTP.md): 사례 단건, 목록(필터, 페이지), 비식별 trace. 역할별 읽기 토큰(`OPS_READ_TOKENS`), 수집 토큰과 분리. `publish_case`는 HTTP로 열지 않음.
- 팩토리 args의 `{env: 이름}` 해석(토큰을 설정 파일에 두지 않음). `release_reader`를 `boundary_contracts.clients:HttpReleaseReader`로 고를 수 있는 형태.
- 이미지 라벨을 io.openit.{service,revision,contracts.boundary,contracts.survey-schema,turn-engine,model-runtime}로 통일.
- 스모크에 읽기 호출, 토큰 분리, 라벨 확인 추가. runbook에 서비스·잡의 경로·변수·설정 키를 한 곳으로 정리.

## 0.2.0 (2026-10-15)

- boundary-contracts v1.2.0: 새 레코드는 pipeline_schema_version "1.1", 읽기는 1.0·1.1 모두.
- FailureCase.question_set: source_release_id의 ReleaseManifest(I4 get_release)에서 채운다. 릴리스를 읽지 못하면 생략하고 사례는 만든다. 읽기 실패 뒤 성공하면 기존 PENDING 사례와 내용이 달라 skipped_diff로 보고된다.
- 검토에서 expected를 적을 때 expected_kind(PARSER|GENERATOR)를 함께 요구한다.
- wheel 스크립트와 requirements의 태그를 v1.2.0으로.

## 0.1.1 (2026-10-15)

- boundary-contracts v1.0.0 패키지를 사용: 복사본(vendor/, _schemas/boundary) 제거, 이미지는 git archive로 만든 wheel 설치. requirements.txt에 태그 의존 기록.
- HTTP 상태 코드를 CONVENTIONS 9절 표와 대조하는 시험 추가.
- list_cases를 SQL 필터로 구현(SQLite). 기존 0.1.0 DB 파일과는 테이블 구조가 달라 호환되지 않는다(배포 전이라 이전 없음).
- 스모크: 고정 포트 제거(호스트 포트 자동 배정), 실행마다 고유 컨테이너·이미지 이름.
- 변경 요청 5건에 헤드 회신 반영.

## 0.1.0 (2026-10-15)

- 설계(`docs/design.md`)와 경계 문서(`docs/boundary.md`) 작성.
- I6 TraceIngest: 봉투와 TurnTrace 검증, `(session_id, turn_id)` 중복 제거, DUPLICATE. SQLite와 메모리 구현. HTTP 서비스(ingest-api)와 클라이언트.
- I7 FailureStore: `publish_case`(멱등, version 연속), `get_case`, `list_cases`, `get_trace`. SQLite와 메모리 구현. 같은 적합성 시험.
- 비식별 처리기: 스키마를 따라 걷는 허용 목록 방식, 의사 식별자, 잔존 패턴 검사(fail-closed). 정책은 설정만.
- 탐지기 인터페이스와 규칙 기반 탐지기, 설정 기반 분류, 사람 검토 큐(version 올림), 롤백 요청 생성.
- 주기 잡 명령 `ops-feedback-job`과 설정 예시. 재처리 결과 동일.
- Dockerfile(비root, 라벨, `/healthz`·`/readyz`), 스모크 시험 스크립트.
- 변경 요청 5건(`docs/change-requests/`).

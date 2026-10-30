# 변경 이력

## 0.18.0 (제안, U400 영역 구조 개편, D117)

- 영역을 컴포넌트로 나눴다(22개, [components.md](components.md), ADR 0025). 코드는 `components/`(컴포넌트마다 파일 하나)와 `wiring/`(연결·주입)으로 옮겼고 컴포넌트끼리 import하지 않는다. 타입은 `schemas/`(40개)에서 `types/`로 생성한다(ADR 0024).
- 받는 쪽 서버는 `openit_link.App/Route`(고정은 requirements.txt, 현재 v0.4.0) 위에 다시 썼다(ADR 0026). 계약 핀 v3.1.1. 와이어 형식·잡 출력은 그대로다. HTTP 동작 변화: 큰 본문 413(전에는 400), 허용하지 않는 메서드 405(전에는 404).
- 진입점은 `api/`, `job/`, `admin/`의 `__main__.py`(`python -m ops_feedback.api|job|admin` 그대로, 콘솔 스크립트 이름 그대로).
- 설정·입력의 거부 조건을 스키마로 옮겼다. 달라진 것: `log.mark`의 상태·해시는 스키마로 검사(전에는 아무 문자열), 롤백 요청 조립·보존 기한 계산이 계약 `OpsFeedbackJobConfig`로 입력을 검사한다. `RuleDetector`에 `REASK_STREAK`를 줄 때 계약대로 `params.threshold`가 필요하다(CR-009).
- 모듈 이름이 바뀌었다(`logstore`->`wiring/log_store`+`components/raw_log_store` 등, 대응표는 design.md 2절).

## 0.18.0 (제안, U400 영역 구조 개편)

- 코드를 컴포넌트(`components/`, 파일 하나에 입력 스키마 하나 -> 출력 스키마 하나)와 조립(`wiring/`)으로 옮겼다. 목록: `docs/components.md`, 이유: ADR 0025. 컴포넌트끼리 import하지 않는다.
- `schemas/`에 영역 내부 타입 40개 JSON Schema와 통과·거부 예시, `src/ops_feedback/types/`에 생성 타입(`scripts/gen_types.py`, README 4절). 거부 조건은 스키마로 옮겼고 옮기지 못한 21건은 `schemas/README.md` 표에 이유와 함께 적었다(ADR 0024).
- 받는 쪽 HTTP는 `openit_link`(D118) 위로 옮겼다(ADR 0026). HTTP 동작 변화: 큰 본문 413(전: 400), 허용하지 않는 메서드 405(전: 404). 와이어 형식·잡 출력·설정 형식은 그대로다.
- 진입점: `python -m ops_feedback.api|job|admin`은 그대로(패키지의 `__main__.py`), 콘솔 스크립트 이름도 그대로.
- 설정 검사: 비밀 직접 기입 금지와 REASK_STREAK 조합은 스키마(`JobConfigChecks`)로. 보존 기본값은 `retention_planner`가 정한다(`load_config`가 더는 설정을 채우지 않는다).
- 계약 핀 v3.1.1, 의존 추가 `openit-link` v0.4.0.
- 변경 요청 CR-009(계약이 REASK_STREAK params.threshold를 필수로 요구).

## 0.17.0 (제안, 계약 3.0.0 전환)

- 계약 핀 v3.0.0, 예시 `pipeline_schema_version` "3.0".
- turn-engine 표지 제거: Dockerfile ARG·라벨 `io.openit.turn-engine`, build-image 인자, 시험의 `turn_engine_version`(→ `action_rules_version`, 값은 계약 규칙 데이터 `load_rules()["rules_version"]`).
- U50: build-image.sh 라벨은 실제 wheel 버전에서, 요청이 고정 버전과 다르면 실패.
- 비식별 설정에서 `scrub_*`, `unlisted_string_action: drop`, `count_map_key_pattern` 경로 제거(ADR 0023). 정책 지문이 바뀐다.

## 0.16.3 (제안, U49: 승격된 계약 스키마의 영역 사본 정리)

`docs/change-requests/` 아래 스키마 파일 53개를 분류해 모두 지웠다. `contract-ready/schemas/`의 6개는 계약 패키지(v2.7.0)의 `OpsFeedback*` 6개와 바이트까지 같은 것을 `cmp`로 확인했다.

| 파일(묶음) | 분류 | 처리 | 근거 |
| --- | --- | --- | --- |
| `schemas/ops-feedback-{deidentified-trace,failurecase-ext,job-config,job-result,readyz,server-env}.schema.json` (6) | (가) 승격된 초안 | 지움 | 계약에 `OpsFeedbackDeidentifiedTrace` 등 같은 6개가 있다(D107). 초안은 이름만 다른 이전 단계 |
| `schemas/examples/*.json` (4), `schemas/validate.py` (1) | 초안의 예시·검증 스크립트 | 지움 | 초안만 읽었다. 코드는 계약 validator를 쓴다(단위 3). 예시는 계약 `conformance/examples`에 있다 |
| `contract-ready/schemas/OpsFeedback*.schema.json` (6) | (가) 계약 사본 | 지움 | 계약 패키지 스키마와 같다(cmp) |
| `contract-ready/examples/valid/*.json` (12), `invalid/*.json` (21) | (가) 계약 예시 사본 | 지움 | 계약 `conformance/examples`, `examples_invalid`가 정본 |
| `contract-ready/{MANIFEST.json,generate_examples.py,validate.py}` (3) | 승격 작업 도구 | 지움 | 승격이 끝났다(계약 `conformance/promoted/ops-feedback.MANIFEST.json`) |
| (나) 계약에 없는 스키마 | 없음 | 남긴 것 없음 | 이 영역은 영역 소유 형식 스키마를 따로 두지 않았다 |

이 사본을 읽는 시험·스크립트·문서 링크: 시험과 스크립트는 없었다. CR-007에 지운 사실을 적었고 README의 CR 개수 표기를 고쳤다.

## 0.16.2 (제안, U47)

- 노출 선언 `deploy-exposure.json`(정본)과 이미지 라벨 `io.openit.exposure`(build-image.sh가 계약 Exposure로 검증해 단다). 시험이 파일과 라우트의 일치를 본다.
- `FailureCase.ext["ops-feedback"]`(잡·검토 게시가 쓴 것)가 계약 `OpsFeedbackFailureCaseExt`를 지키고 계약이 이름 붙인 키 밖을 쓰지 않는다는 것을 시험으로 확인(`tests/test_failurecase_ext.py`). 실행 코드는 바꾸지 않았다.

## 0.16.1 (제안, U43, 계약 v2.6.0)

- `scripts/setup-env.sh`가 표준 출력 마지막 줄에 계약 `SetupEnvReport` 한 줄(area, status, exit_code, seconds, environment{kind, path, test_command})을 낸다. pip 등 진행 기록은 표준 오류로 옮겼다. 실패해도 `status: fail` 보고를 낸 뒤 종료 1. 재시도 대기는 `SETUP_ENV_RETRY_WAIT`(기본 10초)로 줄일 수 있다.
- 계약 핀 v2.6.0, 예시의 `pipeline_schema_version` "2.6"으로 재생성.

## 0.16.0 (제안, U42·U44, 계약 v2.5.1)

- 영역 빌드 진입점 `scripts/build-image.sh`(계약 BuildImageRequest/Result, ADR 0022). `smoke.sh`가 이것으로 이미지를 만든다. `build_wheels.sh`는 폐기 예정.
- 검토 게시 본문의 키·형식 검사를 계약 `CaseReviewRequest` validator로 교체(손 검사 제거). 거부 상태는 같은 400이고 오류 메시지가 `CaseReviewRequest` 위반으로 바뀌는 경우가 있다.
- 계약 핀 v2.5.1, 예시의 `pipeline_schema_version` "2.5"로 재생성.

## 0.15.1 (제안, 단위 4a-B, ADR 0021)

- 시작 때 DDL을 yoyo-migrations 버전 마이그레이션으로 교체(log.db, failures.db 둘 다, SQLite·PostgreSQL). 기존 DB는 그대로 받아들인다. SQLite `:memory:` 경로는 더 이상 열 수 없다.
- 계약 핀 v2.2.2(waitress가 내는 413/414/431을 계약 클라이언트가 INVALID_RECORD로 읽는다).

## 0.15.0 (제안, 단위 4a-A, ADR 0020)

- HTTP 서버를 `http.server` + 직접 만든 `BoundedServer`에서 WSGI 앱 + waitress 3.0.2로 교체. `OPS_MAX_CONNECTIONS`는 같은 이름으로 연결 상한이 된다.
- 동작 변화: 연결 상한 초과는 즉시 503이 아니라 대기, 8 MiB 초과 수집 본문은 400이 아니라 413. 나머지는 기존 시험 그대로.

## 0.14.1 (제안, OF-02, ADR 0019)

- 설정 조합 검사: `REASK_STREAK`이 켜져 있고 `deid.keep_pause_reason`이 꺼져 있으면 설정 오류(잡 종료 코드 2, 이유 메시지). 잡의 모든 설정 오류가 스택 트레이스 대신 종료 코드 2가 된다.
- `REASK_STREAK`은 대화 로그의 연속 횟수를 다시 세지 않고 trace의 `pause_reason == REASK_LIMIT`을 읽는다. 서비스가 정지한 그 턴에 사례가 생긴다(이전에는 한 칸 일렀다). `params.threshold`·`actions`는 무시하고 시작할 때 경고한다(폐기 예정, 계약 `OpsFeedbackJobConfig`에는 아직 필수).

## 0.14.0 (제안, 단위 3: 계약 v2.2.1, 경계 스키마 사용, 문진 스키마 패키지)

- 계약 핀 v2.2.1(`requirements.txt`, 이미지는 `scripts/build_wheels.sh`). turn-engine·model-runtime은 이 영역의 코드·이미지에 의존이 없다(import·설치 0건, 이미지 라벨은 `n/a`).
- 문진 스키마 사본(`src/ops_feedback/_schemas`, `scripts/sync_vendor.sh`, `tests/test_vendor_sync.py`)을 없애고 패키지 `openit-survey-schema`(핀 `survey-schema-v1.1.0`)에서 읽는다. 이미지 빌드는 `build_wheels.sh`가 같은 태그에서 wheel을 만들어 설치한다.
- 계약 경계 스키마(D107)를 코드가 쓴다: 잡 설정은 `OpsFeedbackJobConfig`(모르는 키 거부, 손으로 쓴 형식 검사 제거), 서버 환경 변수는 `OpsFeedbackServerEnv`(모르는 `OPS_` 변수는 시작 거부, 종료 코드 2), 비식별 출력은 `OpsFeedbackDeidentifiedTrace`로 자기 검사(어기면 `DEID_FAILED`로 닫음), `/readyz` 본문은 `OpsFeedbackReadyz`, 잡 stdout은 `OpsFeedbackJobResult`로 자기 검사(어기면 stdout 없이 종료 코드 2).
- 동작 변경: `mask_value_keys`의 null 값도 `[MASKED]`로 가린다(계약이 허용하는 값이 `[MASKED]|[DROPPED]`뿐). 이미 게시된 사례는 재처리하지 않는다. 계약 뷰에 없는 정책 선택지(`scrub_*`, `unlisted_string_action: drop`, 비표준 `count_map_key_pattern`, 64자 넘는 keep 값)는 자기 검사에서 닫힌다: 변경 요청 CR-008.
- 이미지 ARG 기본값을 `n/a`로(`none`·`unknown` 제거). `scripts/smoke.sh`가 빌드된 이미지의 라벨을 `ImageLabels`로 검사한다.

## 0.13.0 (제안, boundary-contracts 2.1.0 CR-H002)

- 정지 사유 `pause_reason`(RISK|REASK_LIMIT|OTHER): 비식별 설정 `deid.keep_pause_reason`(기본 false, 예시 설정 true)으로 열거값만 남기고 모르는 값은 OTHER. 사례 `ext.ops-feedback.pause_reason`, 롤백 평가 지표 `by_pause_reason`. 서비스가 REASK_LIMIT로 이미 호출한 턴의 TURN_FAILURE_SIGNAL·REASK_STREAK는 한 탐지로 합친다. 옛 trace(필드 없음)는 사유 없음으로 읽는다. 켜면 정책 지문이 바뀌고 기존 사례는 재처리하지 않는다. ADR 0014.
- 문진 스키마 스냅샷(`_schemas/survey/v1/TurnTrace.json`)을 survey-service 작업 트리의 `pause_reason` 반영본으로 동기화했다(survey-service f4c3f9b 기준으로 다시 동기화했고 내용은 같았다).

## 0.12.2 (제안, G14)

- 예시 설정에서 `EXTRACT_FAILED`와 `REASK_STREAK`(threshold 3)을 켠다. 세는 방식은 survey-service `_reask_escalation`과 같다: REASK·CONFIRM 합산 연속 턴 수, 문항 무관, 다른 action에서 리셋(이전의 같은 action·같은 next_item 방식 폐기, `actions` 기본값이 [REASK]에서 [REASK, CONFIRM]으로 바뀜). 근거 ADR 0012. HOLD·SKIP의 빈 값은 계약 위반이라 실패로 둔다(코드 변경 없음, 시험 추가).
- 비식별: `deid.keep_count_map_keys`(기본 비어 있음 = 이전처럼 비움)에 적은 건수 맵 필드는 식별자 모양 키와 정수만 남긴다(`count_map_key_pattern`). 예시 설정은 `extract_protections`. ADR 0013. 켜면 정책 지문이 바뀐다.

## 0.12.1 (제안, 독립 재점검 round2)

- M2 검토 게시 이메일 패턴이 제곱 시간이던 것: 검토 입력의 이메일 탐지를 `@` 위치마다 고정 길이 창만 보는 선형 코드로 바꾸고, 전화·주민번호는 한도 있는 정규식 그대로(선형 시험). 문자열 필드 20,000자 상한(초과 400), 검토 본문 1 MiB 상한(수집은 8 MiB 그대로), 중첩 깊이 64. 비식별 결과의 문자열이 4,096자를 넘으면 설정의 잔존 정규식을 돌리지 않고 `DeidError`로 닫는다. 예시 설정의 이메일 잔존 패턴은 길이를 묶었다(deploy 설정도 같게 요청). 이 영역의 모든 정규식을 같은 입력 군(긴 문자열, `@`/`.`/숫자 반복)으로 시험한다.
- M3 `retention.cases_purge_confirmed`(와 `purge_unprocessed`)에 문자열·숫자를 주면 시작을 거부한다(불리언만). 같은 이유로 탐지 규칙의 `enabled`도 불리언만.
- (round3 M1에서 정정) `failed_raw_days`의 기본 90일을 없앴다: 적지 않으면 raw_days를 따른다. 아래 서술의 90일은 폐기된 값이다.
- (round3) 비식별 결과의 긴 문자열은 4,096자 초과에 DeidError로 닫지 않고 창(4,096자, 겹침 1,024)으로 나눠 잔존 패턴을 검사한다(ADR 0011). `admin delete-session --dry-run`이 원본 행도 센다(L3).
- 처리 기한 삭제는 DEID_FAILED·PUBLISH_FAILED(재시도 대상) 원본을 지우지 않는다. 별도 기한 `retention.failed_raw_days`(기본 90일, 임의값)에 지운다.
- `admin delete-session`: 지울 것이 0건이면 이유를 출력하고 종료 코드 3. 비식별 정책에 session_id 의사화가 없어 원래 ID가 남아 있어도 찾는다.
- 잡이 게시할 때 trace가 그 사이 정리되었으면(admin purge의 고아 trace 정리와 겹침) trace를 다시 저장하고 한 번 재시도한다.
- `OPS_REVIEW_REQUIRE_USER=0`이면 `/readyz` `warnings`에 `REVIEW_IDENTITY_NOT_REQUIRED`, 시작 로그에 경고.

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

# CR-007: 영역 경계를 넘는 형식 6개의 스키마 초안 (점검 OF-01, OF-09, OF-11)

상태: 제안(헤드 결정 필요). 작성: ops-feedback, 2026-10-28. 근거: 지시서 1의 B, 점검 보고서 `docs/audit/structure-rules-2026-10-28.md` 6절(E)과 OF-11.
이 CR은 **스키마 초안만** 낸다. 영역 코드는 아직 이 스키마를 쓰지 않는다(헤드가 계약에 올린 뒤의 다음 단계). 초안 파일은 `docs/change-requests/schemas/`에 있다.

## 검증 방법과 결과

```
.venv/bin/python docs/change-requests/schemas/validate.py
```
Draft 2020-12 검증기(`jsonschema` 4.26.0, `FormatChecker` 사용)로 `check_schema`를 통과시킨 뒤 유효 예시와 거부 예시를 돌린다. 거부 예시는 지정한 이유 문자열이 오류 사유에 있어야 통과로 센다. 실제 출력(2026-10-28, 이 세션):

```
OK   job-config           config/job.example.yaml -> 통과
OK   job-config           예시 + rollback + release_reader -> 통과
OK   job-config           거부: retention 키 오타(raw_dayz) -> 거부됨: Additional properties are not allowed ('raw_dayz' was unexpected)
OK   job-config           거부: 최상위 모르는 키 -> 거부됨: Additional properties are not allowed ('rollbak' was unexpected)
OK   job-config           거부: REASK_STREAK에 threshold 없음 -> 거부됨: {'type': 'rule', 'rules': [{'id': 'TURN_FAILURE_SIGNAL'}, {'id': 'TURN_FAILED_STEPS'}, {'i
OK   job-config           거부: retention.raw_days가 null -> 거부됨: None is not valid under any of the given schemas
OK   job-config           거부: rollback.auto_submit이 문자열 -> 거부됨: 'false' is not of type 'boolean'
OK   server-env           서버 env + ARG -> 통과
OK   server-env           거부: 환경 변수 이름 오타 -> 거부됨: Additional properties are not allowed ('OPS_RAW_RETENTON_DAYS' was unexpected)
OK   server-env           거부: 모르는 ARG -> 거부됨: Additional properties are not allowed ('COMMIT_SHA' was unexpected)
OK   server-env           거부: OPS_DB_SCHEMA 값 -> 거부됨: 'skip' is not one of ['apply', 'verify']
OK   deidentified-trace   비식별 trace (실제 잡 출력) -> 통과
OK   deidentified-trace   examples/output.deidentified_trace.json (리포 기존 예시) -> 통과
OK   deidentified-trace   거부: 원본 session_id가 남음 -> 거부됨: 's-1' does not match '^p-[0-9a-f]{32}$'
OK   deidentified-trace   거부: 발화 원문이 남음 -> 거부됨: {'session_id': 'p-c1463533ade1c1a1a59e913284edfce4', 'kind': 'UTTERANCE', 'utterance': '저는
OK   deidentified-trace   거부: 모르는 최상위 키 -> 거부됨: Additional properties are not allowed ('patient_name' was unexpected)
OK   failurecase-ext      ext[ops-feedback] (실제 잡 출력) -> 통과
OK   failurecase-ext      거부: primary_rule 없음 -> 거부됨: 'primary_rule' is a required property
OK   failurecase-ext      거부: pause_reason 열거 밖 -> 거부됨: 'BORED' is not one of ['RISK', 'REASK_LIMIT', 'OTHER']
OK   readyz               /readyz 200 (실제 서버 응답) -> 통과
OK   readyz               /readyz 200 + warnings -> 통과
OK   readyz               /readyz 503 -> 통과
OK   readyz               거부: ready인데 counters 없음 -> 거부됨: {'status': 'ready', 'log_store': 'sqlite', 'failure_store': 'sqlite', 'supported_runtime_c
OK   readyz               거부: 모르는 warning -> 거부됨: {'status': 'ready', 'log_store': 'sqlite', 'failure_store': 'sqlite', 'supported_runtime_c
OK   job-result           잡 stdout (실제 실행) -> 통과
OK   job-result           거부: processed 없음 -> 거부됨: 'processed' is a required property
OK   job-result           거부: errors 항목에 원 turn_id -> 거부됨: 't-1' does not match '^p-[0-9a-f]{32}$'

27건 중 27건 기대대로, 0건 어긋남
```

유효 예시의 출처: `config/job.example.yaml`(리포 파일 그대로), 비식별 trace·`ext`·잡 stdout·`/readyz` 본문은 `config/job.example.yaml` 정책과 `tests/conftest.py`의 `pii_trace`를 실제 `run_once`/`make_server`에 돌려 얻은 출력이다(`schemas/examples/valid.*.json`). 서버 환경 변수 예시는 `docs/boundary.md`·`api.py`에서 이름을 가져와 손으로 조립했다(실제 compose를 읽지 않았다). 거부 예시는 손으로 만든 오류 문서이며 `validate.py` 안에 있다.

한계(확인하지 못한 것): deploy가 실제로 어떤 값을 채우는지는 읽지 못했다. 비식별 trace 스키마는 최상위와 비식별이 직접 건드리는 필드만 정밀하게 적고 안쪽 12개 기록은 object/null로만 본다(문진 스키마 복사본을 만들지 않으려는 선택). 모든 스키마는 현재 코드의 동작을 옮긴 것이며 일부 줄에 '제안'이라고 표시한 불변식만 새 요구다.

## 1. 잡 설정 YAML — `ops-feedback-job-config.schema.json`

- 만들고 읽는 쪽: 만드는 쪽은 deploy(운영 설정)와 이 영역의 예시, 읽는 쪽은 `ops_feedback.job --config`(`config.py load_config`, `DeidPolicy.from_config`, `RuleDetector`, `Classifier`, `RollbackPlanner`).
- 예시: `config/job.example.yaml` 전체(validate.py가 직접 읽는다). rollback·release_reader를 더한 변형도 통과한다.
- 모르는 키 정책: **모든 수준에서 거부(`additionalProperties: false`)**. 이유: 현재 코드는 `retention.raw_dayz` 같은 오타를 조용히 무시하고 기본값(30일)으로 돌아가며, `deid`의 오타는 비식별 기준이 의도와 다르게 적용되게 한다. 지금 코드는 모르는 키를 받아들인다(`load_config`는 필수 최상위 키만 본다). 따라서 스키마를 코드에 적용하면 기존 설정이 거부될 수 있다(예시 파일은 통과).
- 이 영역의 불변식: 기준 값(deid, retention 이외, classification, rollback)은 코드에 기본값이 없다. retention null 거부와 `forever` 명시. 비밀은 설정에 직접 적지 않는다(`{secret: NAME}`만, D84; 이 규칙은 키 이름 검사라 JSON Schema로는 `Factory.args` 설명에만 적었고 스키마가 강제하지 않는다). 제안: `deid.pseudonymize_keys`에 `session_id`를 요구(admin `delete-session`이 비식별 trace를 찾으려면 필요하다. 현재 코드는 요구하지 않고 NOTHING_FOUND 종료 코드 3으로 알린다).
- 알려진 결합: `release_reader.factory`, `gate_service.factory`는 `"모듈:함수"`라 이웃 패키지 경로가 설정에 들어간다(점검 OF-11 7). 계약 클라이언트 이름으로 좁힐지는 헤드 결정이다.

## 2. 서버 환경 변수와 이미지 ARG — `ops-feedback-server-env.schema.json`

- 만들고 읽는 쪽: deploy compose가 만들고 ingest-api(`api.py main`), 저장소(`storage.py`, `pgbase.py`), 잡·admin이 읽는다.
- 예시: 스키마 검증기의 첫 항목(이름은 `api.py`/`pgbase.py`/`storage.py`에서 가져옴). 비밀은 `*_FILE`만 쓴다(D84); 값 변수는 개발용으로 스키마에 남겼다.
- 모르는 키 정책: **모르는 `OPS_*` 변수와 모르는 ARG는 거부**. 이유: `OPS_RAW_RETENTON_DAYS` 같은 오타는 지금 조용히 "선언 없음" 경고로만 나타나고, `OPS_REVIEW_REQUIRE_USER` 오타는 검토자 신원 필수가 켜진 채 남아 의도와 다른 설정이 된다. `OPS_`로 시작하지 않는 변수(PATH 등)는 이 문서 대상이 아니므로 검증할 때 `OPS_` 접두만 넣는다.
- 이 영역의 불변식: 비밀 값은 이미지·설정·로그·`/readyz`·오류 응답에 넣지 않는다. 수집·읽기·검토 토큰은 서로의 경로를 호출할 수 없다. ARG는 라벨 `io.openit.*`(D50)에 기록된다. 값 범위(`OPS_DB_*_TIMEOUT` 0 초과 3600 이하 등)는 코드가 검사하고 스키마는 모양만 본다.
- 확인하지 못한 것: 서버가 읽지 않는 `OPS_DEID_SALT_FILE`을 서버 컨테이너에 줄 필요는 없다(잡·admin 전용). deploy compose의 실제 이름 목록은 읽지 못했다.

## 3. 비식별 TurnTrace — `ops-feedback-deidentified-trace.schema.json`

- 만들고 읽는 쪽: 비식별 처리기(`deid.py`)가 만들고 `GET /v1/deidentified-traces/{ref}`로 synthetic-data, model-lab, 문항 저작이 읽는다. 계약(INTERFACES I7)은 "TurnTrace의 형태를 따르되 스키마 통과 보장 없음"으로만 정의한다.
- 예시: `schemas/examples/valid.deidentified-trace.json`(실제 잡 출력)과 리포의 `examples/output.deidentified_trace.json`(둘 다 통과).
- 모르는 키 정책: 최상위 모르는 키는 **거부**(허용 목록 방식이라 모르는 키는 이미 제거되어 있어야 한다). 안쪽 객체의 모르는 키는 이 초안이 안쪽을 정밀히 다루지 않아 허용한다.
- 이 영역의 불변식: `session_id`/`turn_id`는 `p-<32 hex>` 의사 식별자(제안: 필수. 현재 코드는 정책이 `pseudonymize_keys`에 두지 않으면 원본을 남긴다), 자유 문자열은 `[MASKED]`/`[DROPPED]` 또는 keep 키의 식별자 모양 값, 구조 없는 자유 객체는 비움, 잔존 패턴이 남으면 사례를 만들지 않는다(fail-closed, ADR 0004). 소비자는 마스킹 토큰을 내용으로 쓰지 않는다. 의사 식별자는 되돌릴 수 없다.
- 한계: 안쪽 `session_before` 등 12개 기록은 object/null로만 검사한다. 자유 문자열 잎이 모두 마스킹 토큰이라는 성질은 스키마 한 줄로 표현되지 않아 `frontend_message.utterance` 등 알려진 위치에만 적었다.

## 4. `FailureCase.ext["ops-feedback"]` — `ops-feedback-failurecase-ext.schema.json`

- 만들고 읽는 쪽: 잡이 `detections, primary_rule, deid_policy_id, deid_stats, pause_reason`를, 검토 게시가 `review, reviewed_by`를 쓴다. 이웃은 보존만 하는 것이 원칙이며(다른 영역의 `ext`를 보존하는 것도 같은 규칙), 읽는 이웃이 생기면 이 스키마가 기준이다.
- 예시: `valid.ext-ops-feedback.json`(실제 잡 출력).
- 모르는 키 정책: **읽는 쪽은 무시(허용)**. 이유: `ext`는 계약상 영역별 확장 자리이고 이 영역이 키를 더해도 이웃이 깨지면 안 된다. 쓰는 쪽(이 영역)은 문서에 적힌 키만 쓴다.
- 이 영역의 불변식: `detail`에는 개인정보가 없는 값(규칙 ID, 열거값, 개수)만. `pause_reason`은 `RISK|REASK_LIMIT|OTHER`(PUBLIC_API H절). `deid_stats`는 건수와 경로 목록뿐이다.

## 5. `/readyz` 본문 — `ops-feedback-readyz.schema.json`

- 만들고 읽는 쪽: ingest-api가 만들고 deploy/gateway가 상태 확인에 읽는다(HTTP.md는 상태 코드만 정한다).
- 예시: `valid.readyz-ready.json`(실제 서버 응답)과 503 본문 `{"status":"not-ready","code":"UNAVAILABLE"}`.
- 모르는 키 정책: 읽는 쪽은 **무시**(상태 코드로 판단하고 본문은 보조). 쓰는 쪽은 적힌 키만 쓴다.
- 이 영역의 불변식: 인증이 없는 경로이므로 비밀·개인정보·세션 단위 정보를 싣지 않는다(건수만). `warnings`는 상태 코드에 영향이 없다(`RAW_RETENTION_UNSET`, `REVIEW_IDENTITY_NOT_REQUIRED`). 결과는 `OPS_READYZ_CACHE_SECONDS`(기본 1.5초) 동안 공유된다. 503의 `code` 목록은 BoundaryError 코드 전체를 넣었으나 실제로 나오는 것은 DB 장애(UNAVAILABLE)와 스키마 미적재(FAILED_PRECONDITION)로 확인했다고 말할 수 있는 범위는 코드 읽기까지다(전 코드를 실제로 유발해 보지 않았다).

## 6. 잡 종료 코드와 stdout — `ops-feedback-job-result.schema.json`

- 만들고 읽는 쪽: `ops_feedback.job`이 만들고 deploy 스케줄러가 읽는다.
- 예시: `valid.job-result.json`(실제 `run_once` 보고서).
- 종료 코드(`job.py main` 읽음, 아래 둘은 직접 실행해 확인): 0 오류 없음, 1 보고서 `errors` 비어 있지 않음(일부 실패, stdout 보고서 있음), 2 BoundaryError로 시작·접속 실패(stderr에 JSON 한 줄)나 사용 오류.
- **어긋남(코드는 아직 안 바꿈)**: 잘못된 설정 파일(`ValueError`)이나 없는 설정 파일은 잡히지 않아 트레이스백과 **종료 코드 1**로 끝난다. 실행: `python -m ops_feedback.job --config bad.yaml` -> `ValueError: 설정에 deid 항목이 없다`, exit=1; 없는 파일 -> `FileNotFoundError`, exit=1. 스케줄러는 "일부 턴 실패(1)"와 "설정 오류"를 구별하지 못한다. 제안: 설정 오류는 2로 통일(그래서 이 형식의 종료 코드 표를 계약에 올릴 때 함께 정한다).
- 모르는 키 정책: 읽는 쪽은 **무시**. `errors` 항목은 `additionalProperties: false`이고 `turn`은 의사 식별자 형식이어야 한다(원 turn_id가 보고서에 나가지 않는다는 불변식). stdout 보고서와 `경고:` stderr 줄은 비밀·개인정보를 싣지 않는다.
- 이 영역의 불변식: 같은 입력에 같은 결과, 여러 번 실행해도 안전(멱등, D39). 종료 코드 1은 `errors`가 비어 있지 않을 때뿐이다(스키마는 `$comment`로 적고 강제하지 않음).

## 이 CR이 하지 않는 것

- 영역 코드가 이 스키마를 쓰도록 바꾸지 않았다. 계약(`boundary-contracts`) 반영은 헤드 결정이다.
- OF-02(REASK_STREAK)와 `X-Openit-User`(OF-10)는 다루지 않는다.
- 로그 줄 형식(JSON `ts,lvl,msg`)은 이번 범위(지시서의 목록)에 없어 스키마를 내지 않았다.

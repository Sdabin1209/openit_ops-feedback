# ops-feedback 구조 규칙 R1~R7 전수 점검 보고서 (2026-10-28)

기준서: `head/docs/audit/structure-rules-audit-spec.md`(A~G). 코드·설정·테스트는 수정하지 않았다(이 보고서만 추가). 줄 번호는 점검 시점 HEAD(284e2f3) 기준이다.

## 0. 요약

| 심각도 | 개수 |
| --- | --- |
| P1 | 3 |
| P2 | 10 |
| P3 | 8 |

P1: OF-01 잡 설정이 영역 경계 형식인데 계약·스키마 없음(R6/R5), OF-02 REASK_STREAK이 survey-service 내부 함수·기본값의 복제(R1/R7), OF-03 admin 세션 삭제가 비식별 처리기의 의사 식별자 식을 복제(R3/R7).

돌려서 확인한 것: `.venv/bin/python -m pytest -q -p no:cacheprovider` -> 442 passed, 145 skipped(PostgreSQL), 0 failed(72초, SQLite·메모리 구성, 이 세션에서 직접 실행). 계약 패키지(boundary-contracts)의 `conformance/`는 서버를 띄우는 하네스(`conformance/servers.py`)로, 이 영역 서버에 대해 따로 돌리지 않았다(7절). 이 영역의 `tests/test_http_clients.py`, `tests/test_conformance_*.py`는 위 실행에 포함됐다.

## 1. A. 컴포넌트 목록

읽은 파일: CLAUDE.md, README.md, docs/brief.md, design.md, boundary.md, adr 제목, change-requests 제목, `src/ops_feedback/*.py` 전부(api, job, logstore, failurestore, pgbase 앞부분, deid, detect, review, rollback, admin, config, schemas, storage, clock), config/job.example.yaml, Dockerfile, scripts/build_wheels.sh, sync_vendor.sh. 컴포넌트 경계는 `design.md` 1절 표가 정한 것을 기준으로 코드와 대조했다.

| # | 컴포넌트 | 파일 | 입력 스키마 | 출력 스키마 | 업무 |
| --- | --- | --- | --- | --- | --- |
| 1 | Trace 수집 I6 (로그 저장소 포함) | logstore.py `LogStore.send` | 계약 `TraceEnvelope` + 문진 `TurnTrace`(_schemas/survey/v1) | 계약 `IngestResult`; 저장은 `raw_traces` 표 | 검증, (session_id, turn_id) 멱등 저장 |
| 2 | ingest-api HTTP 서비스 | api.py | HTTP 요청(I6 `POST /v1/traces`, I7 GET, 검토 POST) | `IngestResult`, `FailureCase`, `BoundaryError` | 인증, 라우팅, `/healthz` `/readyz`. 3개 면(수집·읽기·검토)을 한 핸들러가 처리 |
| 3 | 비식별 처리기 | deid.py `Deidentifier` | `TraceEnvelope`(원본 행) + `DeidPolicy`(설정 dict) | `DeidResult(trace, stats)`: 암묵 dict | 허용 목록으로 걷기, 잔존 패턴 검사 |
| 4 | 탐지기 | detect.py `RuleDetector`/`Detector` Protocol | 비식별 trace(dict) | `Detection` 데이터클래스 | 규칙으로 실패 판정 |
| 5 | 분류기 | detect.py `Classifier` | `list[Detection]` + 설정 dict | `Classification` 데이터클래스 | 규칙 -> failure_type |
| 6 | 실패 사례 저장소 I7 | failurestore.py `FailureStoreImpl` | `FailureCase`, 비식별 trace, aux(kind,key,record) | `FailureCase`; 표 `cases`, `traces`, `aux` | 불변·버전 게시, 목록, 보존 삭제 |
| 7 | 검토 큐 | review.py `ReviewQueue` | 검토 결정 + `ExpectedOutput` 계약 스키마 | version+1 `FailureCase` | 검토 게시, PII 패턴 거부 |
| 8 | 롤백 요청 | rollback.py `RollbackPlanner` | `FailureStoreImpl`, `LogStore`, I4 `ReleaseReader`, I5 `GateService`, 정책 dict | 계약 `RollbackRequest`, 계약 `RollbackReceipt`, aux `metric` | 실패율 평가, 요청 생성·전달 |
| 9 | 보존 정리 | 컴포넌트 없음. job.py 199-225, logstore.py `purge_*`/`minimize_processed`, failurestore.py `purge_*`, admin.py `purge` | 설정 `retention` dict | 삭제 건수 | 기한 지난 원본·사례·aux 삭제 |
| 10 | 직원 호출 / pause_reason | 컴포넌트 없음. 직원 호출을 하거나 받지 않는다. detect.py 36-48 `pause_reason_of`(읽기), deid.py 134-153(보존), job.py 168-170(기록), rollback.py 52-54(집계) | TurnTrace의 `TurnFailureSignal.pause_reason` | `FailureCase.ext["ops-feedback"].pause_reason` | 데이터로만 취급. 네 파일에 걸쳐 있음(OF-05) |
| 11 | 잡 엔트리포인트 | job.py `main`, `run_once` | 설정 YAML(스키마 없음) | JSON 보고(`JobReport`) | 위 3~8, 9를 순서대로 한 번 실행 |
| 12 | 관리 CLI | admin.py | 명령줄 | 건수 JSON | 세션 삭제, purge, minimize, migrate |

접착 코드(어느 컴포넌트에도 속하지 않음): job.py `build_from_config`(302-320, 설정 해석·팩토리 import), job.py `run_once`의 FailureCase 조립(149-172), `case_id_for`/`trace_ref` 규칙(job.py 111-112, 147), storage.py(저장소 선택), config.py(설정 로더), schemas.py(검증 도우미), pgbase.py. 컴포넌트 경계가 없는 덩어리: job.py `run_once`(처리, 게시, 보존, 롤백을 한 함수가 함, 115-229).

## 2. 발견 목록

### P1

**OF-01 [R6, R5] 잡 설정은 deploy가 만들어 넘기는 영역 경계 형식인데 계약과 스키마가 없다**
- 위치: config.py:27-60 `load_config`, config/job.example.yaml 전체, docs/boundary.md 258행("운영 잡 설정(deploy 소유)에 `retention: {...}`를 두고").
- 인용: `REQUIRED_TOP = ("storage", "deid", "detection", "classification", "retention")` / boundary.md: "운영 잡 설정(`deploy` 소유)에 ... 두고 서버에 `OPS_RAW_RETENTION_DAYS=30`을 선언하도록 deploy에 요청한다".
- 왜 위반: 설정의 소유·생성자는 deploy(이웃)인데 형식 정의는 이 영역의 `load_config`와 각 `from_config`에 흩어진 수동 검사뿐이다. 기계가 읽는 스키마가 없고 boundary-contracts에도 없다(`schemas/`에서 job/config 이름 없음 확인). 키 목록(`storage.log_db`, `deid.*`, `rollback.*`, `release_reader.factory`, `gate_service.factory`)이 deploy와 암묵적으로 맞물린다. R6(영역 사이 스키마는 헤드 책임). 환경 변수 `OPS_RAW_RETENTION_DAYS`, `OPS_REVIEW_REQUIRE_USER`, `OPS_DB_*_TIMEOUT`, `OPS_MAX_CONNECTIONS` 등 deploy가 설정하는 환경 변수 이름들도 같은 상태(api.py 347-362, pgbase.py 20-23).
- 최소 수정: 헤드가 `JobConfig`(와 서버 환경 변수 목록)를 계약으로 정하거나, 영역 내부 형식으로 선언하고 deploy는 값만 채우는 방식(`config` 전체를 deploy가 통째로 작성하지 않음)으로 분리. 영역 쪽은 `JobConfig` JSON Schema/dataclass를 한 곳에 정의해 `load_config`가 그것으로 검증. 변경 요청으로 올릴 것(아직 `docs/change-requests/`에 없음: CR-001~006에 해당 항목 없음).

**OF-02 [R1, R7] REASK_STREAK이 survey-service 내부 함수와 기본값을 복제한다**
- 위치: detect.py:113-146, config/job.example.yaml(REASK_STREAK 주석), docs/adr/0012.
- 인용: detect.py:116 `정의는 survey-service \`graph.py\`의 \`_reask_escalation\`과 같다(ADR 0012: 같은 사건을 서비스와 탐지가 다르게 세지 않게 한다).` / yaml: `survey-service는 연속 3번 뒤 4번째에 직원 호출로 넘긴다(SURVEY_MAX_CONSECUTIVE_REASK 기본 3, G12)`.
- 왜 위반: 이웃의 내부 함수 이름·환경 변수 이름·기본값에 기대어 같은 규칙(되묻기 연속 세기, COMMITTED 아닌 턴 건너뛰기)을 다시 구현한다. 서비스가 규칙을 바꿔도 이 영역에는 알리는 경로가 없고 임계값 3은 복사본이다. R1과 R7(보증된 값 대신 같은 규칙 재구현). `TurnFailureSignal.pause_reason == "REASK_LIMIT"`(PUBLIC_API H절)이 서비스의 판정을 이미 실어 준다. detect.py 143-145는 그 값을 쓰지만 streak 계산 자체는 별도로 돈다.
- 최소 수정: 서비스가 직원을 호출한 사건은 `pause_reason`으로만 판단하고, 서비스 호출 이전의 "반복 물음" 탐지가 꼭 필요하면 임계값을 계약(또는 TurnTrace 필드)으로 받는다. 코드·설정 주석에서 이웃의 함수·환경 변수 이름을 제거.

**OF-03 [R3, R7] admin의 세션 삭제가 비식별 처리기의 의사 식별자 식을 복제한다**
- 위치: admin.py:40-42, deid.py:314-317, 395-396, job.py:111-112.
- 인용: admin.py:41 `"""비식별 처리기가 session_id 키에 붙이는 의사 식별자(deid.py \`_string\`과 같은 식)."""` / `return "p-" + sha256_hash({"salt": salt, "key": "session_id", "value": session_id})[7:39]`.
- 왜 위반: 삭제(보존) 컴포넌트가 비식별 컴포넌트의 내부 알고리즘(접두어 `p-`, 키 이름, 슬라이스 `[7:39]`)을 직접 안다. 한쪽이 바뀌면 개인정보 삭제가 조용히 0건이 된다(코드가 종료 코드 3으로 완화했으나 원인은 구조). job.py:111-112의 `case_id_for`도 같은 식을 `deid.pseudonym("case", ...)`로 부르는 반면 admin은 `pseudonym` 메서드를 쓰지 않는다. 또 admin.py:48이 `traces_of_session`을 부를 때 `FailureStoreImpl`이 `t.get("session_id")`라는 비식별 trace의 내부 모양에 기대는 것(failurestore.py:415)도 같은 결합이다.
- 최소 수정: 의사 식별자 계산을 `Deidentifier.pseudonym`(또는 별도 모듈) 한 곳에 두고 admin이 그것을 호출. trace의 세션 키 위치는 비식별 출력 스키마에 명시.

### P2

**OF-04 [R5] 컴포넌트 사이 스키마가 암묵 dict다 (DeidResult.trace/stats, 원본 행, aux 레코드, 처리 상태, 설정 dict)**
- 위치: logstore.py:189,271(원본 행 dict 키 `session_id,turn_id,release_id,received_at,envelope,envelope_hash,seq,status`), job.py:130-131 `row["envelope"]`, deid.py:155-160 `DeidResult.stats: dict`, failurestore.py:438 `_AUX_TIME`, rollback.py:90-95(`metric` 레코드, 스키마 없음), job.py:136-197(상태 문자열 `"DEID_FAILED"` 등 리터럴) 대 logstore.py:27 `MINIMIZABLE`.
- 인용: `MINIMIZABLE = ("DONE_CASE", "NO_FAILURE")` / job.py:197 `log.mark(sid, tid, "DONE_CASE", cfg_hash, clock())`.
- 왜: 컴포넌트가 주고받는 형식이 키 관습이다. 처리 상태 열거가 logstore(`MINIMIZABLE`, `FAILED_STATUSES`)와 job(리터럴)에 나뉘어 있다. `aux` 표의 `kind`("metric", "rollback_request", "rollback_receipt")는 failurestore.py와 rollback.py가 문자열로 맞춘다. 정의 위치가 없다. 반면 경계 레코드(`FailureCase` 등)와 `Detection`, `Classification`, `Detector` Protocol은 정의돼 있다(양호).
- 최소 수정: `RawRow`, `ProcessingStatus`(Enum), `DeidResult`의 stats, `MetricSnapshot`을 하나의 `models.py`(타입 있는 dataclass/TypedDict)로. 설정은 OF-01과 함께.

**OF-05 [R3, R5] pause_reason 처리가 네 컴포넌트에 걸쳐 같은 상수와 경로 목록을 따로 가진다**
- 위치: detect.py:36-38, deid.py:136-138, job.py:168-170, rollback.py:52.
- 인용: detect.py:36 `PAUSE_REASONS = ("RISK", "REASK_LIMIT", "OTHER")` / deid.py:136 같은 정의, 경로 목록도 두 번(순서가 다름: detect.py:37 `orchestrator_output` 먼저, deid.py:137 `frontend_input` 먼저).
- 왜: 탐지기가 비식별 컴포넌트의 출력 모양을 독립적으로 알고(같은 trace 위치), 롤백이 job이 쓴 `ext["ops-feedback"].pause_reason` 위치를 관습으로 읽는다(rollback.py:52). 경로 순서 불일치는 값이 둘 이상 있을 때 서로 다른 값을 고를 수 있다(이 세션에서 그 경우를 시험하지 않았다). R3·R5·R7(정규화를 deid와 detect가 각각 구현).
- 최소 수정: 정규화와 경로는 한 곳(예: `pause.py`)에 두고 deid는 "남긴다"만, detect는 이를 읽기만. `ext["ops-feedback"]` 내용은 `OpsFeedbackExt` 스키마(TypedDict/JSON Schema)로 정의.

**OF-06 [R3] 탐지기의 정상 동작이 비식별 정책 설정에 의존한다**
- 위치: detect.py:93-110 `_extract_failed`, docstring "action은 비식별에서 남겨야(keep_string_keys) 읽힌다. 마스킹돼 있으면 (2)는 보수적으로 걸린다.", `_reask_streak`(`action` 비교).
- 왜: 탐지기의 I/O 스키마(비식별 trace)가 정책(`keep_string_keys`에 `action`이 있느냐)에 따라 달라지고 탐지기는 그것을 알지 못한 채 오탐으로 동작한다. 컴포넌트 입력 스키마가 고정돼 있지 않다. job.py의 `retention_warnings`처럼 시작 때 정책-규칙 정합을 검사하지 않는다(확인한 범위: job.py, config.py).
- 최소 수정: "비식별 출력 스키마"(어떤 키가 어떤 값 종류로 남는가)를 정의하고, 탐지기는 그 스키마만 입력으로 받게 하며, 시작 때 정책이 그 스키마를 만족하는지 검사.

**OF-07 [R3] deid.py가 logstore.py의 클래스를 import하고 job이 두 컴포넌트를 직접 이어 붙인다**
- 위치: deid.py:20 `from .logstore import DeidentifierView`, logstore.py:306-313 `DeidentifierView._log._backend`, job.py:129-131, 148 `store.put_trace`.
- 왜: 비식별 컴포넌트가 로그 저장소 모듈의 구체 타입을 알고(Protocol이 아님), `DeidentifierView`는 `LogStore._backend` 비공개 속성에 접근한다. 로그 저장소 주석(logstore.py:4)이 "이 객체를 만드는 코드는 deid.py의 Deidentifier뿐"이라 규정해 규칙이 호출 관습에 의존한다(`tests/test_raw_access.py`로 완화됨, 이 세션에서 통과).
- 최소 수정: `RawReader` Protocol을 두 모듈 밖(공유 타입 모듈)에 정의하고 deid는 그 Protocol만 받는다.

**OF-08 [R3] ingest-api 한 핸들러 클래스가 세 컴포넌트(수집, I7 읽기, 검토)를 `path`로 분기해 처리한다**
- 위치: api.py:144-318 `class H`(`do_GET` 221, `do_POST` 289, `_read_api` 174, `_review` 244). `make_server`(105)가 `LogStore`, `FailureStoreImpl`, `ReviewQueue` 세 개를 클로저로 쥠.
- 인용: api.py:291 `if u.path.startswith("/v1/cases/") and u.path.endswith("/review"): return self._review(u.path)`.
- 왜: 인증 3종과 세 컴포넌트의 로직이 한 클래스에 있고, `ReviewQueue`가 `failures`를 재사용하는 방식(api.py:116, 279-282)으로 api가 FailureStore의 `get_case`와 `ReviewQueue`의 version 비교를 같이 한다. 의도된 설계(ADR 0010)지만 컴포넌트 독립성은 없다.
- 최소 수정: 면별 핸들러(`IngestRoutes`, `ReadRoutes`, `ReviewRoutes`)로 분리하고 공통 인증만 공유.

**OF-09 [R5] FailureCase 조립이 job.py 접착 코드에 있고, 검토 큐가 같은 레코드를 다시 조립한다**
- 위치: job.py:149-172, review.py:111-131, `ext["ops-feedback"]` 키 관습 job.py:161-170, review.py:127-130, rollback.py:52.
- 왜: `ext["ops-feedback"]` 안의 모양(`detections`, `primary_rule`, `deid_policy_id`, `deid_stats`, `pause_reason`, `review`, `reviewed_by`)이 세 파일에 흩어진 키 관습이다. 이웃이 보존만 해야 하는 영역이므로(CONVENTIONS 1절) 자기 영역 컴포넌트 사이 스키마는 R5상 이 영역이 정의해야 한다.
- 최소 수정: `OpsFeedbackExt` 타입 하나.

**OF-10 [F] 검토 게시가 계약에 없는 헤더 `X-Openit-User`를 필수로 요구하고 401이 아닌 403을 낸다**
- 위치: api.py:273-278.
- 인용: `if user is None and require_reviewer_identity: ... raise BoundaryError("PERMISSION_DENIED", "검토자 신원(X-Openit-User)이 필요하다")`.
- 계약: HTTP.md I7 표의 `POST /v1/cases/{case_id}/review` 행에는 `X-Openit-User`가 없다. CONVENTIONS 9절은 "사람 행위자가 필요한 `POST /v1/approvals`·`activate`·`rollback`은 `X-Openit-User`가 없으면 401"로 I5에 한정해 예외를 둔다. 이 영역의 검토는 계약이 정하지 않은 필수 헤더를 서버가 만들고 상태 코드도 다르다. 헤더 형식(`_ID` 정규식, api.py:50)과 `ext.reviewed_by`(review.py:129)는 계약 밖.
- 최소 수정: 변경 요청으로 HTTP.md에 헤더 요구를 명시(코드 상태 코드도 헤드가 401/403 중 확정).

**OF-11 [R1/R6] 영역 경계를 넘는데 계약에 없는 형식 (E절 전체 목록)**
1. 잡 설정 YAML(OF-01).
2. 서버 환경 변수 계약: `OPS_INGEST_TOKEN[_FILE]`, `OPS_READ_TOKENS`, `OPS_REVIEW_TOKENS`, `OPS_LOG_DATABASE_URL`, `OPS_FAILURE_DATABASE_URL`, `OPS_RAW_RETENTION_DAYS`, `OPS_REVIEW_REQUIRE_USER`, `OPS_PORT` 등(docs/boundary.md 292행에서 "deploy compose의 `*_FILE`과 같다"). 계약 문서에는 `<이름>_TOKENS` 규칙만 있고 이름은 영역 문서가 정한다는 위임(HTTP.md 공통)이므로 위임 범위 안이지만, deploy가 이름을 알아야 하는 결합이다.
3. 이미지 빌드 인터페이스: Dockerfile `ARG COMMIT/CONTRACTS_VERSION/SURVEY_SCHEMA_VERSION/TURN_ENGINE_VERSION/MODEL_RUNTIME_VERSION`, `build/wheels` 선행 필요(`scripts/build_wheels.sh`의 `BOUNDARY_TAG`). 라벨 키는 워크스페이스 규칙(D50)이 정하므로 계약은 있으나 ARG 이름은 deploy와의 암묵 약속.
4. `FailureCase.ext["ops-feedback"]`의 내용(OF-09): 이웃은 보존만 해야 하지만 읽는 이웃이 생길 수 있음.
5. 비식별 trace `GET /v1/deidentified-traces/{ref}`의 본문: 계약은 "TurnTrace의 형태를 따르되 스키마 통과 보장 없음"(INTERFACES I7)이라 이웃이 쓸 수 있는 구조 보장이 없다. 마스킹 토큰 `[MASKED]`, `[DROPPED]`(deid.py:29-30)와 의사 식별자 `p-...` 형식은 계약에 없다.
6. 집계 응답: `/readyz` 본문(`counters`, `warnings`, `supported_runtime_contract_versions`, api.py:231-241)은 deploy/gateway가 읽을 수 있으나 계약에 없다(HTTP.md는 상태 코드만).
7. 이웃이 만들어 넘기는 팩토리 이름: 설정 `release_reader.factory`, `gate_service.factory`(`"모듈:함수"`, job.py:316-318). 이웃 패키지 경로가 설정에 들어간다(OF-01에 포함).

**OF-12 [R1] 문진 스키마를 리포 안에 복사본으로 두고 이웃 폴더를 읽는 스크립트와 시험이 있다**
- 위치: scripts/sync_vendor.sh:8-10, tests/test_vendor_sync.py:14-16, src/ops_feedback/_schemas/survey/v1/*, schemas.py:19 `SCHEMA_ROOT`.
- 인용: `S=../survey-service/schema` / `cp "$S"/schemas/TurnTrace.json src/ops_feedback/_schemas/survey/v1/TurnTrace.json`.
- 판단: 읽기 전용 참조 `../survey-service/schema`는 워크스페이스가 허용한다(D 허용 목록). 다만 CLAUDE.md 공통 규칙은 계약 스키마의 복사본을 만들지 않는다고 한다(문진 스키마는 CR-004에서 패키지화 요청 중으로 docs/change-requests/CR-004에 있음, 읽은 범위: 제목만). 이 복사본은 정수 버전 1 안의 변경을 자동 반영하지 못해 G11(D79)처럼 어긋날 수 있다. 시험은 원본이 없으면 xfail(README 5절).
- 최소 수정: CR-004 해결(문진 스키마 패키지) 전까지 현행 유지. 심각도 P2(알려진 임시 결합).

**OF-13 [R7] 이중 검증·재구현**
- 위치: api.py:186, 210, 281 `validate_record("FailureCase", ...)`: `FailureStoreImpl.publish_case`(failurestore.py:355)가 쓰기 때 이미 검증한 레코드를 읽을 때 다시 검증. logstore.py:358 자기가 만든 `IngestResult`를 다시 검증. rollback.py:107-109 `validate_record("RollbackReceipt", ...)`와 request_id 대조: 이웃(I5) 응답을 재검증(HTTP 클라이언트는 이미 읽기 모드 검증을 한다. HTTP.md 공통).
- 왜: R7은 내보낸 값을 믿는다고 한다. 영역 바깥에서 오는 값(I5 응답 receipt)은 SDK 클라이언트(`HttpGateService` 등)가 이미 검증하므로 중복이다. 다만 `GateService`가 Protocol 구현(메모리 등)일 때를 대비한 방어일 수 있다(근거 기록은 rollback.py:108 주석 "원칙 10"). 그 `원칙 10`의 출처 문서는 이 세션에서 읽지 못했다.
- 최소 수정: 읽기 경로의 재검증은 시험(적합성)으로 대체하고 서버는 저장된 레코드를 믿는다. I5 응답 검증은 SDK에 맡기거나 이유를 ADR에 남긴다.

### P3

**OF-14 [F]** 알 수 없는 경로 응답이 `BoundaryError` 레코드가 아니다. api.py:242, 294 `self._json(404, {"error": "not found"})`. CONVENTIONS 9절 "실패는 `BoundaryError` 레코드를 본문으로 낸다". 경로 오타 수준이지만 클라이언트는 `code`를 못 읽는다.

**OF-15 [F]** `limit`이 500을 넘으면 clamp가 아니라 400(api.py:195-196). HTTP.md 공통은 "limit(기본 100, 최대 500)"만 정한다. 어느 쪽이 맞는지 계약이 말하지 않는다(확인하지 못한 것: 다른 영역 서버의 동작).

**OF-16 [F]** `updated_since`에서 `+`가 공백으로 오는 쿼리 문자열을 코드가 보정한다(api.py:202 `.replace(" ", "+")`). 클라이언트가 인코딩하지 않는 것을 서버가 흡수. 계약에 인코딩 규칙이 없음.

**OF-17 [F]** `/readyz` 성공 본문이 인증 없이 건수 카운터와 경고를 노출한다(api.py:231-241). 계약은 상태 코드와 2초 상한만 정한다(CONVENTIONS "상태 확인 응답 시간"). 노출 필드는 계약 밖이며 개인정보는 없다.

**OF-18 [R1]** 코드·ADR 주석이 이웃 문서·데이터를 인용한다: detect.py:89,98 "survey-service design.md 18절 ... 학습 데이터 5,077행 전수 일치", pgbase.py:19 "survey-service outbox". 근거 표기이지 의존은 아니다. 값(`DEFAULT_EMPTY_VALUES_OK_ACTIONS = ["REDIRECT"]`)이 이웃 문서 한 줄에 근거하고 계약에는 `ActionExtraction` 스키마만 있다(boundary-contracts V2_DRAFT.md 2.2를 detect.py가 인용). 이 영역은 데이터를 직접 보지 않았다고 스스로 적었다.

**OF-19 [R3]** `Detector.factory`(detect.py:216-218, 설정의 `"모듈:함수"`)와 `release_reader.factory`(job.py:316)가 설정 문자열로 임의 모듈을 import한다. 설정 파일이 신뢰 경계 안에 있다는 가정(ADR 기록 없음, 확인 범위: docs/adr 제목). 컴포넌트 교체 방법으로는 양호하나 입력 스키마(`args`)가 팩토리마다 암묵적이다.

**OF-20 [R1]** 이미지 빌드가 `../boundary-contracts`의 git 저장소에서 `git archive`로 wheel을 만든다(scripts/build_wheels.sh:7). 워크스페이스 규칙은 `../boundary-contracts`를 읽기 전용으로 허용하므로 R1 위반은 아니나, 규칙 본문은 `pip install git+ssh://...@<태그>`가 표준이다(Docker 안 ssh 불편이라는 이유가 스크립트 주석에 있음). `deploy` 경로 언급은 메시지 문자열뿐.

**OF-21 [G]** `_question_set`이 I4 응답의 `AttributeError/TypeError/KeyError/ValueError`를 모두 삼킨다(job.py:73). 이웃 응답을 믿지 않는 방어 코드이며 R7의 "재검증"에 해당한다. 같은 클라이언트가 이미 `INTEGRITY_ERROR`로 형식 이상을 알린다(boundary.md 267행도 그렇게 기술).

## 3. B. R3·R4 점검 결과 요약

확인 방법: 각 `src/ops_feedback/*.py`의 `import` 문과 `grep -n "_backend\|\._log\|\._store\|\._b\b"`로 비공개 속성 접근을 찾았다.
- 다른 컴포넌트의 비공개 속성 접근: logstore.py:313 `self._log._backend`(OF-07). 다른 곳은 찾지 못했다.
- `role`/종류 분기로 한 함수가 두 일을 함: api.py `H`(OF-08), job.py `run_once`(접착, 11행 설명). `FailureStoreImpl.put_aux/get_aux/list_aux`가 `kind` 문자열로 지표·요청·영수증 3종을 한 표에서 다룸(failurestore.py:438-459, OF-04).
- 전역 상태: 모듈 수준 `lru_cache`(schemas.py `_registry`, `_validator`, `trace_schema`), 프로세스 안 카운터(api.py:117 클로저 지역). 컴포넌트 간 공유 전역 상태는 찾지 못했다.
- 설정 필드 직접 사용: `Classifier(cfg["classification"])`, `DeidPolicy.from_config(cfg["deid"])`는 각자 자기 절만 받는다(양호). 단 job.py:121-122 `cfg_hash`가 `cfg["detection"]`·`cfg["classification"]`을 직접 해시해 세 컴포넌트의 설정 구조에 기댄다(`deid.policy.fingerprint()`는 메서드 경유).
- 공유 DB 표: `raw_traces`는 logstore.py만, `cases/traces/aux`는 failurestore.py만 SQL을 가진다(grep `raw_traces`, `FROM cases` 확인). 다른 컴포넌트는 메서드로만 접근한다(양호, R3 충족).

## 4. C. R5 점검 요약

- 기계가 읽는 정의가 있는 것: 경계 레코드(boundary-contracts 스키마, schemas.py `validate_record`), 문진 `TurnTrace`(_schemas), `Detector` Protocol·`Detection`·`Classification`(detect.py), `_RawBackend`/`_Backend` ABC.
- 없거나 암묵인 것: 원본 행 dict, `DeidResult.stats`, `ext["ops-feedback"]`, aux 3종 레코드(특히 `metric`: rollback.py:90-95), 처리 상태 열거, 설정 전체(OF-01, 04, 05, 09). 표 DDL은 SQLite(logstore.py:143-152, failurestore.py:153-162)와 PostgreSQL(logstore.py:235-244, failurestore.py:241-250)에 각각 문자열로 중복되어 있다(파일 내에서 두 벌, 같은 내용인지는 눈으로만 비교).

## 5. D. R1 점검 결과

검색한 패턴: `grep -rn "\.\./" src scripts Dockerfile Makefile tests`, `grep -rn "survey_service|survey-service|release_gate|model_lab|SURVEY_|release-gate" src --include=*.py`, import 문 전수.
- 이웃 패키지 import: 없음. import하는 외부 패키지는 `boundary_contracts`(계약), `jsonschema`, `referencing`, `psycopg`, `yaml`(확인).
- 이웃 폴더 경로: scripts/sync_vendor.sh:8(`../survey-service/schema`, 허용), scripts/build_wheels.sh:7(`../boundary-contracts`, 허용), tests/test_vendor_sync.py:15(허용). `../deploy`는 메시지 문자열뿐이며 읽지 않음.
- 이웃 구조에 기댐: OF-02(survey-service 내부 함수·환경 변수 이름), OF-18(이웃 문서 인용), TurnTrace 내부 키(`extract_output`, `session_before.dialogue_log`, `log_entry.turn_result`, `persistence_commit_result` 등: detect.py 28-146)는 문진 스키마(허용된 읽기 대상)에 정의된 것이라 R1 위반이 아니다. 단 v2 TurnTrace 키 이름은 "확정 전"으로 README 5절 D-E에 남아 있다.
- 이웃 호스트·포트: 코드에 하드코딩 없음(`release_reader.args.base_url`은 설정이며 예시는 주석 처리됨: job.example.yaml 하단 `http://release-gate:8080`은 주석 속 예시로 이웃 서비스 이름·포트를 담고 있다. P3 참고).

## 6. E. R6 (경계 인터페이스별 계약 유무)

| 인터페이스 | 방향 | 계약 스키마 | 비고 |
| --- | --- | --- | --- |
| I6 `POST /v1/traces` | 제공 | `TraceEnvelope`, `IngestResult`, HTTP.md I6 | 있음 |
| I7 `GET /v1/cases`, `/v1/cases/{id}`, `/v1/deidentified-traces/{ref}` | 제공 | `FailureCase`, HTTP.md I7 | 비식별 trace 본문 스키마는 "구조 보장 없음"으로만 정의 |
| 검토 `POST /v1/cases/{id}/review` | 제공 | HTTP.md I7 표(Protocol 밖) | `X-Openit-User`는 계약에 없음(OF-10) |
| I4 ReleaseReader | 소비 | `ReleaseManifest`, `ActivePointer`, `boundary_contracts.sdk.ReleaseReader` | 있음. 실제 SDK 연결은 시험 대역뿐(README 1절) |
| I5 `request_rollback` | 소비 | `RollbackRequest`, `RollbackReceipt` | 있음. 게이트의 request_id 멱등은 이 영역이 확인하지 못함(boundary.md 272행) |
| 잡 설정, 서버 환경 변수, 이미지 ARG | deploy와 | 없음 | OF-01, OF-11 |
| 비식별 정책(`deid` 절) | 사용자/deploy가 값 결정 | 없음 | 정책 값이 FailureCase 내용에 영향 |
| `ext["ops-feedback"]` | 이웃에 노출 | `ext`는 계약, 내용은 없음 | OF-09 |

영역 안에서 정의됐지만 경계를 넘는 스키마: 비식별 trace의 모양(마스킹 토큰, `p-` 의사 식별자), `/readyz` 본문, `ext["ops-feedback"]`, 로그 줄(JSON `ts,lvl,msg`: api.py:58-62, deploy가 수집할 수 있음).

## 7. F. 계약 대조 (한 줄씩)

대조 문서: boundary-contracts HTTP.md(공통, I6, I7), INTERFACES.md I6·I7, CONVENTIONS.md 3·4·6·9절, `schemas/IngestResult`, `FailureCase`. 근거 코드: api.py.

| 항목 | 계약 | 구현 | 판정 |
| --- | --- | --- | --- |
| I6 경로·메서드 | `POST /v1/traces` | api.py:293 | 일치 |
| I6 성공 | 200 + `IngestResult` | api.py:311 `self._json(200, res)` | 일치 |
| I6 ACCEPTED/DUPLICATE | 먼저 받은 것 유지 | logstore.py:353-357 | 일치(시험 통과) |
| I6 입력 검증 | trace는 `runtime_contract_version`의 TurnTrace로 검증, 위반 INVALID_RECORD | logstore.py:340-351 | 일치 |
| I6 버전 불일치 | `UNSUPPORTED_VERSION` 422 | logstore.py:342-344, api.py:45-46 | 일치 |
| 오류 상태 코드 표 | INVALID_RECORD 400, PERMISSION_DENIED 403, NOT_FOUND 404, CONFLICT 409, FAILED_PRECONDITION 412, UNSUPPORTED_VERSION·INTEGRITY_ERROR 422, UNAVAILABLE 503 | api.py:45-46 `HTTP_STATUS` | 일치 |
| 인증 | Bearer, 토큰 없거나 틀리면 403(영역 사이 API) | api.py:295-297 | 일치 |
| 토큰 역할 분리 | 읽기 토큰은 쓰기 경로 403 | api.py:114-115, 247-249, `_read_auth` 168-172 | 일치(세 종류 토큰 서로 호출 불가) |
| I7 `get_case` | `?version=` 생략 시 최신 | api.py:180-187 | 일치 |
| I7 `list_cases` 쿼리 | review_status, failure_type, source_release_id, updated_since, latest_only(true\|false) | api.py:189-211 | 일치. 기본 latest_only=true는 INTERFACES 시그니처와 같음 |
| 목록 응답 | `{items, next_cursor}`, limit 기본 100 최대 500 | api.py:211, 192-196 | 일치(500 초과는 400, OF-15) |
| I7 `get_trace` | 비식별 TurnTrace JSON | api.py:212-213 | 일치 |
| `publish_case` HTTP 비공개 | 열지 않음 | api.py: POST는 `/v1/traces`와 review뿐 | 일치 |
| 검토 게시 | 201 새 version / 200 재전송 / 404 / 읽기 토큰 403 / body 키 4개 | api.py:244-287 | 일치. 단 `X-Openit-User` 필수화는 계약 밖(OF-10) |
| `expected` 검증 | LORA_A ActionExtraction, LORA_B ResponseText | review.py:15-17, 142-157 | 일치 |
| `/healthz` `/readyz` | 있음, readyz는 의존 불가 시 2초 안에 503 | api.py:225-241, boundary.md 224·241행 | 일치(측정은 README 4절 기록, 이 세션 미측정) |
| 멱등 일반 규칙 | 같은 ID와 같은 내용 -> 기존 것, 다른 내용 CONFLICT | failurestore.py:354-376 | 일치 |
| `BoundaryError` 본문 | 모든 실패 | api.py:166 `e.to_record()`; 404 경로 오류는 예외(OF-14) | 일부 불일치 |
| 타임아웃 값 | 서비스 총 마감 등은 survey-service 몫 | 이 영역 서버: 요청 30초(api.py:90), DB 문장 3초(pgbase.py:20) | 계약에 이 영역 서버 값은 없음. ADR 0008이 근거 표기 |
| I5 소비 | `request_rollback`, 같은 request_id 멱등 | rollback.py:85, 104-111 | 이 영역 쪽은 일치. 게이트 쪽 멱등은 확인하지 못함 |
| `RollbackRequest.evidence_refs` | ops-feedback이 해석하는 불투명 Id | rollback.py:94-99 `metric-<hash>` | 일치. 지표 스냅샷을 이웃이 읽을 방법은 없음(CR-002에 기록) |
| `pause_reason`(PUBLIC_API H) | 읽는 쪽은 모르는 값을 OTHER로 | detect.py:41-48, deid.py:141-145 | 일치. 복제됨(OF-05) |

적합성 시험: 계약 패키지 `conformance/servers.py`를 이 영역 서버에 연결해 돌리지 않았다(7절). 이 영역의 `tests/test_http_clients.py`는 `boundary_contracts.clients`의 실제 클라이언트로 이 서버를 호출하며 통과했다.

## 8. G. R7 점검 요약

OF-02(되묻기 연속 세기 재구현), OF-03(의사 식별자 식 재구현), OF-05(pause_reason 정규화 두 번), OF-13(재검증), OF-21(I4 응답 방어). 반대로 `Deidentifier` -> `Detector` 사이에서 탐지기가 비식별 결과를 재검증하는 코드는 찾지 못했다(detect.py 전체 읽음). `FailureStoreImpl.publish_case`가 `deidentified_trace_ref`의 존재를 확인하는 것(failurestore.py:366)은 같은 저장소 안의 참조 무결성 검사이며 R7 대상이 아니라고 판단했다.

## 9. 문제없음으로 확인한 항목

- 원본(log.db) 읽기 경로는 `grant_deidentifier_view` 하나이며 `tests/test_raw_access.py`가 소스 수준에서 막는다(시험 통과 확인, 코드 읽음: logstore.py:306-313, 401-403).
- 두 저장소의 SQL은 각 모듈 안에만 있다(`grep -rn "raw_traces\|FROM cases" src`로 확인).
- 이웃 영역 코드 import 없음, 이웃 호스트·토큰 하드코딩 없음(비밀 값은 읽지 않았고 `read_secret` 경유 확인).
- 경계 레코드는 쓰기 전에 계약 스키마로 검증(failurestore.py:355, rollback.py:100, logstore.py:340).
- 탐지기는 비식별 뒤 trace만 입력으로 받는 구조(`Detector.detect(deid_trace)`).

## 10. 점검하지 못한 것

- `pgbase.py` 40행 이후, `deid.py`는 전부 읽었으나 `tests/` 전체와 `docs/adr/*.md` 본문, `docs/runbook.md`, `docs/STATUS.md`, `docs/CHANGELOG.md`, `docs/change-requests/CR-001~005` 본문은 읽지 않았다(CR-006과 ADR 0014 일부만 읽음). ADR의 근거가 OF-02, OF-13의 판단을 바꿀 수 있다.
- PostgreSQL 경로(145건 건너뜀)는 실행하지 못했다. PG 시험·`scripts/smoke.sh`·Docker 이미지 빌드는 실행하지 않았다.
- boundary-contracts `conformance/`를 이 서버에 연결해 돌리지 않았다. `METRICS.md`, `V2_DRAFT.md`, `PUBLIC_API.md`(H절 외), `PUBLIC_API`의 직원 호출 부분은 읽지 않았다. "직원 호출" 컴포넌트는 이 영역에 없다는 것은 `grep -rn "staff\|Staff" src`(코드 4건, TurnTrace 필드 읽기뿐)로 확인했다.
- 이웃(survey-service)의 실제 `_reask_escalation` 구현은 읽지 않았다(워크스페이스 규칙으로 허용되지 않음). OF-02는 이 영역 주석이 말하는 내용에 근거한다.
- `deploy` 쪽 설정(OF-01, OF-11)은 읽지 않았다. deploy가 실제로 어떤 키를 채우는지는 확인하지 못했다.
- DDL 두 벌(SQLite/PostgreSQL)의 내용 일치는 눈으로만 비교했고 자동 확인은 하지 않았다.

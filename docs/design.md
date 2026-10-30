# ops-feedback 내부 설계

상태 표기: **확인**(코드와 시험으로 확인), **제안**(구현했으나 사용자·헤드 확정 전), **미정**(결정 전이라 구현하지 않음).

## 1. 구성과 데이터 흐름

```
survey-service --I6 send--> [ingest-api] --> LogStore(log.db, 원본)
                                                  | (비식별 처리기만 읽는다)
                                                  v
                          주기 잡 ops-feedback-job (한 번 실행하고 끝)
                          Deidentifier -> Detector(들) -> Classifier -> FailureStore.publish_case
                                                  |                             |
                                       비식별 trace 저장 ------------------> FailureStore(failures.db)
                                                                                 |  I7
                  ReviewQueue (version+1 게시) <----------------------------------+--> synthetic-data / model-lab / 문항 저작
                  RollbackPlanner --I4 읽기--> release-gate(ReleaseReader)
                                  --I5 request_rollback--> release-gate(GateService)
```

컴포넌트(입력 스키마 하나 -> 출력 스키마 하나)와 파일, 입출력 타입은 [components.md](components.md)가 정본이다(U400, [ADR 0025](adr/0025-component-decomposition.md)). 이 절의 옛 모듈 이름(`logstore.py` 등)은 아래와 같이 옮겨졌다.

| 옛 이름 | 지금 |
| --- | --- |
| `logstore.py` (로그 저장소 + 수집) | `components/raw_log_store.py`(저장), `components/trace_intake.py`·`ingest_receipt.py`(수집), `wiring/log_store.py`(수집 조립, `LogStore`) |
| `api.py` (ingest-api) | `components/link_ingest_api.py`(받는 쪽, [ADR 0026](adr/0026-openit-link-for-link-components.md)), 진입점 `api/__main__.py`, 서비스 면 `wiring/ingest_service.py` |
| `deid.py` | `components/deidentifier.py`(비식별), `components/pseudonymizer.py`(의사 식별자) |
| `detect.py` | `components/failure_detector.py`(탐지), `components/failure_classifier.py`(분류) |
| `review.py` | `components/review_decider.py`(결정), `wiring/review_flow.py`(조립) |
| `failurestore.py` | `components/failure_case_store.py` |
| `rollback.py` | `components/rollback_assessor.py`·`release_predecessor.py`·`rollback_request_builder.py`, 이웃 통신 `link_release_read.py`·`link_gate_send.py`, 조립 `wiring/rollback_flow.py` |
| `job.py` | `wiring/pipeline.py`(잡 조립), `components/case_builder.py`·`case_reconciler.py`·`retention_planner.py`·`job_advisor.py`, 진입점 `job/__main__.py` |

지표 대시보드와 피드백 배포기는 이번 구간 범위 밖이다(미정). 대시보드는 FailureStore의 목록과 `RollbackPlanner`의 지표 스냅샷만 읽으면 되도록 비식별 뒤의 데이터만 둔다.

## 2. 저장소 (O10: 영역이 정한다)

저장소 두 곳(원본, 실패 사례)을 인터페이스 뒤에 두고 구현을 둘 둔다(`_RawBackend`, `_Backend`). **운영은 PostgreSQL 서버(서비스별 데이터베이스·계정)**, 개발·시험 기본은 SQLite 파일이다. PostgreSQL을 운영 표준으로 한 이유: 영역별 파일 DB보다 다중 프로세스 동시 쓰기, 백업·복구, 계정 권한 분리가 표준 도구로 해결된다(사용자 결정). 선택은 환경으로만 한다: `OPS_LOG_DATABASE_URL`, `OPS_FAILURE_DATABASE_URL`(비밀, `_FILE` 우선)이 있으면 PostgreSQL, 없으면 `OPS_LOG_DB`, `OPS_FAILURE_DB`의 SQLite. 원본과 실패 사례는 **서로 다른 데이터베이스(계정)**를 가리키며 분리 원칙은 그대로다(원본 백엔드는 `components/raw_log_store.py`, 실패 사례 백엔드는 `components/failure_case_store.py`에만 있다). 같은 테이블 구조를 시작 시 버전 마이그레이션(yoyo-migrations, ADR 0021)으로 만들고, 연결이 끊기면 다시 열어 한 번 재시도하며(쓰기는 모두 멱등), JSON은 TEXT로 저장해 SQLite와 같은 바이트를 낸다. 중복(DUPLICATE), 보존 삭제, 처리 표시는 구현이 달라도 같은 적합성 시험을 통과한다.

- `log.db`(로그 저장소, 원본): `raw_traces(seq, session_id, turn_id, release_id, received_at, envelope, envelope_hash, status, config_hash, processed_at)`, `UNIQUE(session_id, turn_id)`. WAL 모드. 쓰기 계정은 ingest-api와 잡뿐이고, 읽는 컴포넌트는 비식별 처리기뿐이다. 두 파일을 따로 두는 이유는 파일·볼륨 권한으로 분리하기 위해서다(대시보드·검토 큐에는 `failures.db`만 준다).
- `failures.db`(실패 사례 저장소, 비식별 뒤): `cases(seq, case_id, version, record)`, `traces(ref, trace)`, `aux(kind, key, record)`(지표 스냅샷, 롤백 요청·영수증).

메모리 구현은 같은 클래스에 `MemoryBackend`를 끼운 것이다. 로직(검증, 멱등, 버전 규칙, 필터, 페이지)은 한 곳에 있어 두 구현이 같은 적합성 시험(`tests/test_conformance_*.py`)을 통과한다.

`list_cases`의 필터·최신 version·페이지는 SQLite에서 SQL로 처리한다(인덱스: review_status/failure_type/source_release_id, source_release_id/created_at). 메모리 구현은 같은 의미를 파이썬으로 구현하고 같은 시험을 통과한다.

## 3. 수집 (I6)

`LogStore.send(envelope)`:
1. `TraceEnvelope` 스키마로 검증(실패 시 `INVALID_RECORD`).
2. `runtime_contract_version`이 지원 목록(`survey_schema.available_versions()`)에 없으면 `UNSUPPORTED_VERSION`.
3. `trace`를 그 버전의 `TurnTrace` 스키마로 검증, `trace.session_id/turn_id/contract_version`이 봉투와 같아야 한다(`INVALID_RECORD`).
4. `(session_id, turn_id)`로 `INSERT OR IGNORE`. 새로 들어가면 `ACCEPTED`, 있으면 `DUPLICATE`(먼저 받은 것 유지, `received_at`도 처음 값).
5. `IngestResult`를 스키마로 검증해 돌려준다. 저장소 오류는 `UNAVAILABLE`(재시도 가능).

수집은 비식별과 탐지를 하지 않는다(턴을 막지 않기 위해 한 번의 INSERT만 한다). 탐지·비식별은 잡이 한다.
HTTP 형식은 CR-005. 토큰은 `OPS_INGEST_TOKEN_FILE`(파일 경로, D84)이 우선이고 없으면 `OPS_INGEST_TOKEN`(환경 변수, 개발용)이다.

## 4. 비식별 처리기

### 4.1 원칙

- 원본은 비식별 처리기만 읽는다. `LogStore.grant_deidentifier_view()`가 유일한 읽기 경로이고, 잡은 `Deidentifier.pending()`으로만 원본을 얻는다. `tests/test_raw_access.py`가 소스 수준에서 다른 모듈의 접근을 막는다.
- **허용 목록 방식**이다. 문진 스키마의 `TurnTrace` 정의를 따라 걸으며 허용한 것만 남기고 나머지는 지운다. 어떤 필드에 개인정보가 들어오는지 전부 알 수 없으므로 "알려진 개인정보를 지우는" 방식을 쓰지 않는다.
- 비식별 기준(남길 키, 정의 목록, 패턴)과 보관 기간은 사용자가 정한다. 코드에 기본값이 없고 설정이 없으면 시작하지 않는다(`DeidPolicy.from_config`가 필수 키를 요구한다). `config/job.example.yaml`의 값은 시험·시연용 보수적 예시일 뿐이다.

### 4.2 개인정보가 들어올 수 있는 필드 (TurnTrace v1에서 읽은 목록, 확인)

`TurnTrace`는 한 턴의 모든 컴포넌트 입출력을 담고, 같은 환자 정보가 여러 곳에 복사되어 있다. 자유 문자열(열거값·상수가 아닌 `type: string`)이 있는 곳이 모두 후보다.

| 분류 | 필드 (정의.필드) | 내용 |
| --- | --- | --- |
| 환자 발화 원문 | `FrontendMessage.utterance`, `UtteranceParserInput.utterance`, `AnswerJudgeInput.utterance`, `ResponseGeneratorInput.utterance` | STT 결과. 이름, 연락처, 증상 서술 등 무엇이든 가능 |
| 발화에서 잘라낸 조각 | `Item.source_text`, `Item.value`, `TextSpan.text`, `Evidence.text`, `Request.source_text[]`, `Judgment.said_value` | 발화의 일부, 환자가 말한 값 |
| 환자 답변 값 | `Value`(문자열 / `{amount, unit, unit_code}` / `CodedValue`)를 쓰는 모든 곳: `SayValue.value`, `SlotRecord.current_value`·`history`·`pending.value`, `StoreDecision.value`, `ChangedSlot.previous_value`·`new_value`, `Notice.value`·`previous_value`, `ItemCheck.standard_value`, `Judgment.value_now` | 건강 정보. 숫자도 포함 |
| 시스템이 환자에게 한 말 | `TurnTrace.response_output`, `FrontendInput.response_text`, `ResponseRequestRecord.next`(LoRA-B 입력 사본) | 값이 문장에 섞여 들어간다 |
| 오류 메시지 | `ProblemDetail.message`, `SessionCommitResult.detail` | 저장소 오류에 값이 섞일 수 있다 |
| 세션 전체 상태 | `session_before`, `session_after`(`SessionStore`: `answer_records`, `dialogue_log`, `progress_record`, ...) | 지금까지의 모든 답변과 대화 기록 |
| 구조가 없는 자유 객체 | `pendings`, `current_values`, `question_slots`, `target_records`, `awaiting`, `correction_requests`, `deferred`, `unknown_refused_records`, `created_pendings`, `resolved_pendings`, `*_counts` 등 `type: object`(properties 없음) | 내용을 스키마로 알 수 없다 |
| 식별자 | `session_id`(모든 중첩 위치), `turn_id` | 시스템 ID지만 외부 정보와 연결될 수 있다 |
| 문항 정의 | `QuestionBank`(`question_sentence`, `choice_catalog`, ...) | 개인정보는 아니나 릴리스 산출물이고 거대하다 |

### 4.3 처리 방식 (확인: `tests/test_deid.py`)

`walk(data, schema)`가 스키마 노드마다 다음을 적용한다.

1. 정책 `drop_defs`에 든 정의(v2 TurnTrace에는 쓸 곳이 없어 예시 설정은 빈 목록)는 `"[DROPPED]"`, `mask_defs`에 든 정의(예: `Value`)는 `"[MASKED]"`로 통째로 바꾼다. 환자 답변 값은 숫자 객체까지 이 경로로 가린다.
2. `oneOf/anyOf`는 값의 모양(상수, 열거, 타입, 필수 키, 상수 판별 필드)으로 가지를 고른다. **맞는 가지가 없으면 제거**한다(문자열이면 마스킹).
3. 열거값·상수 문자열은 남긴다(구조 정보: `Action`, `intent`, `status` 등).
4. 그 밖의 문자열은 키 이름으로 판단한다. `pseudonymize_keys`(`session_id`, `turn_id`)는 `p-` + salt를 넣은 `canonical.sha256_hash`로 바꾼다(같은 입력은 같은 값, salt는 환경 변수). `keep_string_keys`(구조 식별자: `question_id`, `slot_id` 등)는 남긴다. 나머지는 `"[MASKED]"`로 바꾼다(`unlisted_string_action`은 `mask`만: 3.0.0에서 `scrub_*`와 `drop`이 계약 밖 출력이라 없어졌다, ADR 0023). 어느 쪽이든 길이를 남기지 않는다.
5. 키가 `mask_value_keys`에 있으면 타입과 상관없이(숫자·불리언·객체 포함) 값을 `"[MASKED]"`로 바꾼다. v2의 `extract_output.values[].value`는 스키마가 형태를 정하지 않아(모델이 낸 값) 숫자·불리언 환자 답변이 `Value` 정의를 거치지 않고 지나가므로 이 키(`value`)를 지목한다. 정책이 지목한 `mask_defs`·`drop_defs` 정의가 문진 스키마에 없으면 `DeidError`(스키마가 바뀌었는데 정책이 조용히 효력을 잃는 것을 막는다).
5-1. 숫자·불리언·null은 남긴다(인덱스, 오프셋, 횟수). 키가 `mask_number_keys`에 있으면 null로 바꾼다.
6. 스키마에 없는 키는 제거한다(키 이름도 값일 수 있어 기록하지 않고 개수만 센다). `properties`가 없는 자유 객체는 `{}`로 비운다.
7. 마지막으로 결과의 모든 문자열 잎(마스킹 토큰 제외)에 `residual_patterns`(이메일, 전화, 주민번호 등 정책 제공)를 검사한다. 걸리면 `DeidError`로 **실패로 닫는다**: 그 Trace는 FailureCase를 만들지 않고 `DEID_FAILED`로 표시한다(`--retry-deid-failed`로 정책을 고친 뒤 다시).

결과의 통계(`deid_stats`)에는 건수와 **스키마 경로**(예: `extract_input.user_utterance`)만 담고 값은 담지 않는다. 사용자는 이 경로 목록으로 무엇이 가려졌는지 검토해 정책을 조정한다.

한계(**확인**): 이름 같은 자유 서술 속 개인정보는 패턴으로 못 잡는다. 그래서 기본 경로는 "자유 문자열 전부 마스킹"이다. 발화 문장을 남기려면(`keep_string_keys`에 추가) 이름 식별 방법을 사용자가 따로 정해야 한다. 비식별 결과는 `TurnTrace` 스키마를 통과하지 않을 수 있다(CR-001).

### 4.4 결정론

같은 봉투와 같은 정책이면 같은 결과다(시각·난수를 쓰지 않는다). 정책 지문(`DeidPolicy.fingerprint`)은 `canonical`로 계산해 잡의 설정 해시에 포함한다.

## 5. 실패 탐지

```python
class Detector(Protocol):
    name: str
    def detect(self, deid_trace: dict) -> list[Detection]: ...   # Detection(rule_id, method, detail)
```

- 탐지기는 **비식별 뒤 trace만** 입력으로 받는다. 원본이 필요한 탐지기는 만들 수 없는 구조다.
- `detail`에는 규칙 ID, 열거값, 개수만 담는다(개인정보 없음).
- 규칙 기반 `RuleDetector`의 규칙(구조 필드만 본다, **확인**): `TURN_FAILURE_SIGNAL`(턴 실패 신호), `TURN_FAILED_STEPS`(`turn_result.failed_steps`), `COMMIT_ERROR`(`persistence_commit_result.status != OK`), `RESPONSE_FALLBACK`(`response_outcome == "fallback"`, v1 이름 `GENERATOR_FALLBACK`에서 개명, ADR 0015), `STAFF_ALERT_DELIVERY_FAILED`, `INTERVENTION_FAILED`, `EXTRACT_FAILED`, `REASK_STREAK`(임계값은 설정, 기본값 없음). 어떤 규칙을 켤지는 설정이 정한다.
  - `EXTRACT_FAILED`(v1 `PARSER_EMPTY` 대체): (1) `extract_status`가 있고 `OK`가 아니다(OUTPUT_INVALID, VIOLATION, ERROR). (2) `OK`인데 `extract_output.values`가 비고 `action`이 `params.empty_values_ok_actions`(기본 `["REDIRECT"]`)에 없다. 근거: survey-service design.md 18절 "첫 값의 문항, 값이 없으면(REDIRECT)"(학습 데이터 5,077행 전수 일치) 즉 REDIRECT만 값이 비는 것이 정상이다 **[확인: 그 문서. 데이터는 직접 보지 않았다]**. HOLD·SKIP은 계약상 targets가 필수이고 targets는 같은 행 values의 key라 값이 비면 계약 위반이다 **[확인: ActionExtraction 스키마, V2_DRAFT 2.2]**: 허용 목록에 더하지 않는다(ADR 0012). `action`은 비식별에서 남겨야 하므로 `keep_string_keys`에 있어야 한다.
  - `REASK_STREAK`(v1 `UNRECOGNIZED_STREAK` 대체): 연속 횟수를 세지 않고, 서비스가 trace에 남긴 `pause_reason == REASK_LIMIT`인 턴에서 걸린다(서비스가 정지한 그 턴). `params`는 무시한다(폐기 예정). 이유와 대안은 ADR 0019(0012 결정 1을 대체).
  - 옛 이름 `PARSER_EMPTY`·`UNRECOGNIZED_STREAK`·`GENERATOR_FALLBACK`은 뜻이 달라져 받지 않는다(설정에 있으면 대체 이름을 알리는 오류, `enabled:false`여도). `TURN_FAILED_STEPS`는 `phase:step` 문자열을 그대로 남기므로 새 `step` 값(`LoRA-A`, `VALIDATE`, `COMMIT`, `DELIVER_STAFF`, `LoRA-B`, `DELIVER_FRONT`)에서도 그대로 동작한다(시험으로 확인).
- 교체: 설정의 `detection.detectors`에 `{factory: "모듈:함수", args: {...}}`로 다른 `Detector`(예: 모델 기반, 사람 신고)를 더한다. 반환한 `Detection.method`가 `FailureCase.detection_method`(RULE, MODEL, HUMAN, FEEDBACK)가 된다.
- 규칙이 놓치는 것(**미정**): 의미적 오류(잘못 이해했지만 구조는 정상인 턴)는 규칙으로 못 잡는다. 사람 신고(FEEDBACK/HUMAN) 경로와 모델 탐지기는 후속 구간이다.

## 6. 사례 분류

`failure_type`은 문자열이다. 분류체계(O11)는 사용자 결정 전이라 코드에 값이 없다. `Classifier`는 설정의 `rule_to_type`(규칙 ID -> 문자열), `priority`(여러 규칙이 걸릴 때 대표 규칙 순서), `default_failure_type`(필수)만 쓴다. 탐지된 규칙 전체는 `ext["ops-feedback"].detections`에 남는다(분류가 바뀌어도 근거가 남는다). 같은 입력·같은 설정이면 같은 분류다.

## 7. FailureCase 생성과 멱등

- `case_id` = `fc-` + `canonical` 해시(salt 넣은 session/turn 의사 식별자)의 앞 32자. 원본 ID를 쓰지 않는다.
- `deidentified_trace_ref` = `tr-` + 비식별 trace의 `canonical` 해시. 같은 내용은 같은 ref. 저장소는 case를 받을 때 ref의 trace가 먼저 저장돼 있어야 한다(`FAILED_PRECONDITION`).
- `created_at` = 원본의 `received_at`(잡 실행 시각이 아니다). `source_release_id` = 봉투의 `release_id`(필수). `runtime_contract_version` = 봉투 값. `deidentified`는 항상 true(스키마가 const로 강제).
- `question_set`(선택): 설정에 `release_reader`가 있으면 I4 `get_release(source_release_id).question_set`을 채운다. 읽기 실패(`BoundaryError`)면 생략한다. 릴리스별로 한 번만 읽는다. 검토 version은 이 값을 보존한다.
- `ext["ops-feedback"]`: `detections`, `primary_rule`, `deid_policy_id`, `deid_stats`.
- 같은 Trace를 두 번 처리하면: 사례가 이미 있고 내용이 같으면 그대로(`cases_existing`). 설정이 바뀌어 내용이 달라졌고 아직 PENDING v1이면 건드리지 않고 `skipped_diff`로 보고한다(불변 레코드, 검토를 덮지 않는다). 검토가 끝난 사례도 건드리지 않는다.
- 처리 표시: 원본 행의 `status`(`DONE_CASE`, `NO_FAILURE`, `DEID_FAILED`, `PUBLISH_FAILED`)와 설정 해시. 게시 중 `UNAVAILABLE`이면 표시하지 않아 다음 실행에서 다시 한다. `--reprocess`는 표시를 지우고 전부 다시 돌린다(결과는 같다. `tests/test_job.py`).

## 8. 사람 검토 큐

큐는 별도 저장소가 아니라 `review_status = PENDING`인 최신 사례의 목록이다(`ReviewQueue.pending`). 검토는 `review(case_id, CONFIRMED|DISMISSED, reviewer_role, ...)`이 version을 올린 새 레코드를 게시한다. 이전 version은 그대로다. 같은 검토를 다시 보내면 새 version을 만들지 않는다. `base_version`을 주면 그 사이에 바뀐 경우 `CONFLICT`. `expected`(정답 출력)는 CONFIRMED에만 붙일 수 있고, 검토자가 비식별 뒤의 trace를 보고 쓴 값이어야 한다(저장소는 내용을 판별하지 못한다. 검토 UI는 후속). 검토자 정보는 `ext["ops-feedback"].review`에 둔다. 다른 영역이 넣은 `ext`는 보존한다.

## 9. 롤백 요청 생성

`RollbackPlanner.evaluate()`(잡의 마지막 단계, 설정에 `rollback`과 `release_reader`가 있을 때만):
1. I4 `get_active_pointer()`로 현재 릴리스 R. 없으면 아무것도 안 한다.
2. 창(`window_hours`) 안의 R의 총 턴 수(로그 저장소의 릴리스별 건수, 원본 내용 아님)와 실패 사례 수(`counted_review_statuses`에 든 것)를 센다.
3. `min_turns` 미만이면 표본 부족, 실패율이 `max_failure_rate` 이하면 아무것도 안 한다.
4. 초과하면 I4 `list_releases`(최신부터)에서 R 바로 앞 릴리스를 되돌릴 대상으로 고른다. 없으면 요청하지 않는다.
5. 지표 스냅샷 `metric-<해시>`를 저장하고 `evidence_refs`에 넣는다(해석 방법은 CR-002). `request_id` = `rb-` + `(from, to, pointer_version)` 해시라서 같은 포인터 상태에서는 같은 요청이다(게이트의 멱등과 맞물림).
6. `RollbackRequest`를 스키마로 검증해 저장. `auto_submit`이 true이고 `GateService`가 주어졌으면 `request_rollback`으로 보내고 `RollbackReceipt`를 검증해 저장한다. `UNAVAILABLE`이면 다음 실행에서 같은 request_id로 다시 보낸다.

롤백은 요청만 한다. 포인터는 게이트만 바꾼다. 기준 수치는 모두 사용자 결정이라 기본값이 없다.

## 10. 잡

`ops-feedback-job --config <파일> [--limit N] [--reprocess] [--retry-deid-failed] [--no-rollback]`는 한 번 실행하고 JSON 보고(처리 수, 게시 수, 기존 수, 정상 수, 비식별 실패 수, 설정 변경으로 건너뜀, 원본 삭제 수, 롤백 평가)를 표준 출력에 낸다. 오류가 있으면 종료 코드 1. 주기는 외부 스케줄러(cron 등)가 정한다(D39). 보관 기간 `retention.raw_days`가 숫자면 그보다 오래된 원본을 삭제하고, `null`(미정)이면 지우지 않는다. 실패 사례·비식별 trace의 보관은 미정이다(사례는 불변 레코드이고 다른 영역이 참조한다. 삭제 정책이 필요하면 헤드와 정한다).

## 11. 외부 의존과 대역

- I4 ReleaseReader, I5 GateService는 `boundary_contracts.sdk`의 Protocol로만 받는다(생성자 인자). 시험용 대역은 `tests/fakes.py`에 직접 만들었다. 실제 SDK는 설정의 `release_reader.factory`, `gate_service.factory`("모듈:함수")로 연결한다.
- 모든 경계 호출의 실패는 `BoundaryError`다. 저장소 오류는 `UNAVAILABLE`로 감싼다.
- 해시는 `boundary_contracts.canonical`만 쓴다. 레코드는 쓰기 전에 `schemas.validate_record`로 검증한다.

## 12. 미정과 가정

- 비식별 기준(남길 키, 패턴), 보관 기간, 실패 유형 분류체계(O11), 롤백 기준, 허용 중단 시간(O16): 사용자 결정. 설정으로만 받는다.
- 운영 저장소는 PostgreSQL 서버(사용자 결정). 복제·고가용성 구성은 O16 결정 뒤. SQLite는 개발·시험용.
- 수집 가용성: 서비스 쪽이 영속 버퍼와 재시도를 갖는다(I6 합의). ingest-api는 단일 프로세스 waitress(WSGI) 스레드 서버다(ADR 0020).

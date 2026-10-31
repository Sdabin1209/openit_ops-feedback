# 컴포넌트 (U400, D117)

입력은 이 영역이 계약(boundary-contracts v3.1.2)과 [브리프](brief.md)에서 받고 내는 것이다. 그 일을 업무 단계로 나누어 컴포넌트를 정했다. 기존 코드는 빠진 업무 규칙과 거부 조건을 찾는 대조 자료로만 썼다.
한 컴포넌트 = 입력 스키마 하나 -> 출력 스키마 하나(`schemas/`의 같은 이름 파일, 타입은 `src/ops_feedback/types/`에서 생성). 컴포넌트끼리는 import하지 않는다. 어느 출력을 어느 입력에 넘길지는 `wiring/`의 몫이다.
계약 타입은 복사하지 않고 `$ref`로 가리킨다(`../boundary/<이름>.schema.json`). 왜 이렇게 나눴는지: [ADR 0025](adr/0025-component-decomposition.md). 스키마가 못 다루는 조건: [schemas/README.md](../schemas/README.md).

## 업무 흐름

수집(I6): TraceEnvelope -> `trace_intake` -> `raw_log_store` -> `ingest_receipt` -> IngestResult.
주기 잡(D39): `raw_log_store`(원본, 비식별 단계만 읽음) -> `deidentifier` -> `failure_detector` -> `failure_classifier` -> `case_builder` -> `case_reconciler` -> `failure_case_store`(+ `link_release_read`로 문항 세트) -> 보존 정리(`retention_planner`, `job_advisor`) -> 롤백 평가.
롤백 평가: `link_release_read` -> `rollback_assessor` -> `release_predecessor` -> `rollback_request_builder` -> `failure_case_store`(보조 기록) -> `link_gate_send`.
검토(I7 면): `link_ingest_api` -> `review_decider` -> `failure_case_store`. 관리 도구: `erasure_planner`, `erasure_summary`, `purge_planner`, `pseudonymizer`.

## 표

| 컴포넌트 | 파일 | 입력 타입 | 출력 타입 | 하는 일 |
|---|---|---|---|---|
| 수집 | `components/trace_intake.py` | `TraceIntakeRequest` | `RawTraceRecord` | TraceEnvelope 하나를 원본 기록으로 만든다. 지원 버전, 문진 스키마(TurnTrace) 적합, envelope-trace ID·버전 일치를 거른다 |
| 수집 결과 | `components/ingest_receipt.py` | `IngestReceiptRequest` | 계약 `IngestResult` | ACCEPTED/DUPLICATE와 받은 시각(중복이면 처음 받은 시각)을 만든다 |
| 로그 저장소 | `components/raw_log_store.py` | `RawTraceRecord`, `RawStatusMark` | 저장된 기록(처리 표시, 건수) | 원본을 (session_id, turn_id)로 중복 없이 저장하고 처리 표시·보존 삭제·최소화를 한다. 원본 읽기는 `DeidentifierView`로만 열린다 |
| 의사 식별자 | `components/pseudonymizer.py` | `PseudonymRequest` | `Pseudonym` | HMAC-SHA256(salt)으로 값을 의사 식별자로 바꾼다(식의 유일한 정의) |
| 비식별 | `components/deidentifier.py` | `DeidRequest` | `DeidResult` | 문진 스키마를 따라 걸으며 허용한 것만 남기고 잔존 패턴이 있으면 닫는다(fail-closed). 계약 비식별 뷰를 지키는지 자기 검사 |
| 실패 탐지 | `components/failure_detector.py` | `DetectRequest` | `DetectionReport` | 비식별 trace에서 규칙(교체 가능한 탐지기)으로 실패를 찾고 정지 사유를 읽는다 |
| 사례 분류 | `components/failure_classifier.py` | `ClassifyRequest` | `Classification` | 탐지 결과를 설정의 대응표·우선순위로 failure_type에 매긴다 |
| 사례 조립 | `components/case_builder.py` | `CaseBuildRequest` | `CaseBuildResult` | FailureCase(version 1)와 저장할 비식별 trace·참조를 만든다(입력에서만 정해져 멱등) |
| 사례 대조 | `components/case_reconciler.py` | `ReconcileRequest` | `ReconcileDecision` | 이미 있는 version 1과 비교해 게시/기존 유지/설정 변경으로 건너뜀을 정한다 |
| 실패 사례 저장소 | `components/failure_case_store.py` | `FailureCase`(계약), `AuxRecord` | 저장된 사례·trace·보조 기록 | 불변 사례를 version 순서로 저장하고 조회·삭제·보존 정리를 한다(I7) |
| 검토 결정 | `components/review_decider.py` | `ReviewRequest` | `ReviewOutcome` | 사람 검토를 version을 올린 새 사례로 만든다(같은 검토는 변경 없음). 개인정보 패턴을 거른다 |
| 롤백 판단 | `components/rollback_assessor.py` | `RollbackAssessRequest` | `RollbackAssessment` | 창·상태로 사례를 세어 실패율이 기준을 넘었는지 판단한다 |
| 되돌릴 릴리스 | `components/release_predecessor.py` | `PredecessorRequest` | `PredecessorResult` | 최신부터의 릴리스 목록에서 현재 바로 앞(더 오래된) 릴리스를 찾는다 |
| 롤백 요청 조립 | `components/rollback_request_builder.py` | `RollbackPlanRequest` | `RollbackPlan` | 계약 RollbackRequest와 근거 지표 스냅샷을 만든다(request_id는 입력에서만 정해져 멱등) |
| 보존 기한 | `components/retention_planner.py` | `RetentionRequest` | `RetentionCutoffs` | 설정의 보존 기간(기본값 포함)을 지울 기준 시각으로 바꾼다 |
| 설정 알림 | `components/job_advisor.py` | `AdvisorRequest` | `Advisories` | 무기한 보관·검증 없는 keep 키·기한 넘긴 미처리 원본을 경고로 알린다 |
| 삭제 대상 | `components/erasure_planner.py` | `ErasureRequest` | `ErasureTargets` | 세션 삭제 때 비식별 trace에서 찾을 session_id 값(의사 식별자·원래 값)을 정한다 |
| 삭제 요약 | `components/erasure_summary.py` | `ErasureCounts` | `ErasureSummary` | 삭제 건수를 요약하고 0건이면 확인 안내를 단다 |
| purge 기준 | `components/purge_planner.py` | `PurgeRequest` | `PurgeCutoffs` | 관리 도구 purge의 일 수를 기준 시각으로 바꾼다(기간은 사용자가 정한다) |
| 수집·읽기·검토 서버(받는 쪽) | `components/link_ingest_api.py` | HTTP 요청(`TraceEnvelope`, `CaseReviewRequest`, `ListCasesQuery`, `ReviewHeaders`) | HTTP 응답(`IngestResult`, `FailureCase`, 목록, 비식별 trace, readyz) | `openit_link.App/Route` 위에 경로·토큰별 역할·쿼리 검증·`/readyz`를 적는다. 인증·상한·계약 검증·오류 본문은 openit_link가 한다 |
| 릴리스 읽기(보내는 쪽) | `components/link_release_read.py` | 릴리스 ID | 문항 세트 ref, 활성 포인터, 릴리스 ID 쪽 | release-gate의 읽기 SDK(I4)를 부른다. 한 번 UNAVAILABLE이면 이번 실행에서는 더 부르지 않는다 |
| 롤백 요청 전달(보내는 쪽) | `components/link_gate_send.py` | `RollbackRequest`(계약) | `RollbackReceipt`(계약) | release-gate에 요청을 보내고 영수증이 계약·요청 ID에 맞는지 확인한다(I5) |

## 컴포넌트가 아닌 것

| 위치 | 하는 일 |
|---|---|
| `wiring/` | 조립: `pipeline.py`(잡 한 번), `rollback_flow.py`, `review_flow.py`, `log_store.py`(수집 조립), `ingest_service.py`(서버에 주입하는 서비스 면), `admin_flow.py`, `build.py`(설정에서 조각 만들기), `storage.py`(저장소 고르기), `check_settings.py`, 그리고 도구 `validate.py`(스키마 검증), `clock.py`, `pgbase.py`(PostgreSQL 연결) |
| `config.py`, `migrate.py`, `migrations/` | 설정 읽기, DB 마이그레이션(업무 규칙 없음) |
| `api/__main__.py`, `job/__main__.py`, `admin/__main__.py` | 진입점: 서버 기동, 잡 한 번, 관리 명령(인자·종료 코드·출력만) |
| `types/` | `schemas/`에서 생성한 타입(손으로 고치지 않는다) |

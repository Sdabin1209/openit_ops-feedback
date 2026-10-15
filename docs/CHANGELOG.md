# 변경 이력

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

# 변경 이력

## 0.2.0 (2026-10-15)

- boundary-contracts v1.1.0: 새 레코드는 pipeline_schema_version "1.1", 읽기는 1.0·1.1 모두.
- FailureCase.question_set: source_release_id의 ReleaseManifest(I4 get_release)에서 채운다. 릴리스를 읽지 못하면 생략하고 사례는 만든다. 읽기 실패 뒤 성공하면 기존 PENDING 사례와 내용이 달라 skipped_diff로 보고된다.
- 검토에서 expected를 적을 때 expected_kind(PARSER|GENERATOR)를 함께 요구한다.
- wheel 스크립트와 requirements의 태그를 v1.1.0으로.

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

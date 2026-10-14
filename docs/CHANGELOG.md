# 변경 이력

## 0.1.0 (2026-10-15)

- 설계(`docs/design.md`)와 경계 문서(`docs/boundary.md`) 작성.
- I6 TraceIngest: 봉투와 TurnTrace 검증, `(session_id, turn_id)` 중복 제거, DUPLICATE. SQLite와 메모리 구현. HTTP 서비스(ingest-api)와 클라이언트.
- I7 FailureStore: `publish_case`(멱등, version 연속), `get_case`, `list_cases`, `get_trace`. SQLite와 메모리 구현. 같은 적합성 시험.
- 비식별 처리기: 스키마를 따라 걷는 허용 목록 방식, 의사 식별자, 잔존 패턴 검사(fail-closed). 정책은 설정만.
- 탐지기 인터페이스와 규칙 기반 탐지기, 설정 기반 분류, 사람 검토 큐(version 올림), 롤백 요청 생성.
- 주기 잡 명령 `ops-feedback-job`과 설정 예시. 재처리 결과 동일.
- Dockerfile(비root, 라벨, `/healthz`·`/readyz`), 스모크 시험 스크립트.
- 변경 요청 5건(`docs/change-requests/`).

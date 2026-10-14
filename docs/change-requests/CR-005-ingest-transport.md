# CR-005 I6 수집의 전송 규약(HTTP) 확정

- 요청자: ops-feedback
- 대상: `INTERFACES.md` I6, `CONVENTIONS.md` 3, 6절
- 상태: 제안(구현함, 확정 필요)

I6은 `send(envelope) -> IngestResult`만 정하고 서비스 API의 전송 형식이 없다. survey-service와 맞추기 위해 다음으로 구현했다.

- `POST /v1/traces`, 본문 `TraceEnvelope` JSON, 헤더 `Authorization: Bearer <서비스 토큰>`. 성공은 200과 `IngestResult`(ACCEPTED 또는 DUPLICATE).
- 실패는 `BoundaryError` 레코드(JSON 본문)와 HTTP 상태: INVALID_RECORD 400, PERMISSION_DENIED 403, FAILED_PRECONDITION 412, UNSUPPORTED_VERSION 422, UNAVAILABLE 503 등(CONVENTIONS 3절 코드별 매핑은 `api.py`의 `HTTP_STATUS`).
- `GET /healthz`, `GET /readyz`.
- 클라이언트 `ops_feedback.client.HttpTraceIngest`는 `TraceIngest` Protocol을 지키며 실패를 `BoundaryError`로 던진다.

헤드가 이 형식을 계약(CONVENTIONS 6절 또는 별도 문서)에 올릴지 알려 주기 바란다. 또 서비스 토큰 발급·회전 방식(환경 변수 `OPS_INGEST_TOKEN` 하나로 시작했다)이 정해지면 반영한다.

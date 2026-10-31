# CR-011: OpsFeedbackServerEnv에 요청 전체 마감 이름이 없다

- 영역: ops-feedback. 대상 계약: `OpsFeedbackServerEnv`(boundary-contracts v3.1.2).
- 무엇이 부족한가: D120은 서버 쪽 요청 전체 마감을 "설정(환경 변수)으로 받고 기본값 9.0초"로 정했다. 그런데 `OpsFeedbackServerEnv`는 `OPS_` 접두의 모르는 이름을 거부하고(오타 방지) 이 마감의 이름이 없어, 영역이 환경 변수를 새로 받으면 서버가 기동을 거부한다.
- 제안: `OPS_REQUEST_DEADLINE_SECONDS`(`SecondsStr`)를 `OpsFeedbackServerEnv.env`에 더한다(추가만). 반영되면 영역이 `api/__main__.py`에서 읽어 `ServerSettings`로 검증한다.
- 지금: 기본값 9.0초를 코드 상수(`wiring/ingest_service.REQUEST_DEADLINE_SECONDS`)로 쓰고, 환경 변수로는 바꿀 수 없다.

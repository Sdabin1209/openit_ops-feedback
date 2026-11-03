# CR-012. 롤백 근거 상태는 CONFIRMED만 (OpsFeedbackJobConfig.rollback.counted_review_statuses)

- 영역: ops-feedback. 대상 계약: `OpsFeedbackJobConfig`(boundary-contracts v3.2.0), conformance 예시 `OpsFeedbackJobConfig.rollback.json`.
- 문제: 계약은 `counted_review_statuses`를 임의 문자열 배열로 받고 conformance 유효 예시가 `PENDING`을 포함한다. 수집 토큰만 있으면 위조 Trace가 PENDING 사례가 되어 사람 검토 없이 롤백 요청을 만들 수 있다(L07, CR-006). 이 영역은 ADR 0029로 CONFIRMED만 받도록 영역 스키마(`JobConfigChecks`)에서 더 좁게 거부한다.
- 요청: (1) `counted_review_statuses.items`를 `{"const": "CONFIRMED"}`(또는 enum)로 좁히고 설명에 이유를 적는다. (2) 유효 예시에서 `PENDING`을 뺀다. 그때까지 영역은 계약 유효 예시 하나를 거부한다(시험이 그 사실을 고정한다).
- 영향: 다른 영역의 설정 생성자(deploy)가 PENDING을 넣고 있으면 거부된다. 확인하지 못했다.

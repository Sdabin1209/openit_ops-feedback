# CR-009: OpsFeedbackJobConfig가 REASK_STREAK의 params.threshold를 아직 필수로 요구한다

- 영역: ops-feedback. 대상 계약: `OpsFeedbackJobConfig`의 `$defs/Rule`(boundary-contracts v3.1.2).
- 무엇이 부족한가: `REASK_STREAK` 규칙은 서비스가 trace에 남긴 `pause_reason=REASK_LIMIT`을 읽을 뿐 횟수를 다시 세지 않는다(ADR 0019). 그런데 계약은 `params`와 `threshold`를 필수로 요구해, 쓰지 않는 값을 설정에 적어야 한다. U400에서 탐지 컴포넌트가 규칙 설정을 계약으로 검증하게 되자 `{"id": "REASK_STREAK"}`만으로는 거부된다.
- 제안: `REASK_STREAK`일 때 `params`와 `threshold`를 선택으로 바꾼다(받던 설정을 더 받는 완화). 그 전에는 예시 설정처럼 `params: {threshold: 3, actions: [...]}`를 적는다.
- 영향: 이 영역의 동작은 바뀌지 않는다(잡은 이미 계약으로 설정을 검증했다).

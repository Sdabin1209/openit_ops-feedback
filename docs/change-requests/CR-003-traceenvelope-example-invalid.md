# CR-003 TraceEnvelope 예시의 trace가 TurnTrace 스키마를 통과하지 않는다

- 요청자: ops-feedback
- 대상: `boundary-contracts/examples/TraceEnvelope.json`, `verify.py`
- 처리 결과(헤드 회신): 결정: 헤드 예시의 결함이었고 수정됨.
- 이전 상태: 확인된 사실

`examples/TraceEnvelope.json`의 `trace`는 `{contract_version, session_id, turn_id}` 세 필드뿐이다. `survey-service/schema/schemas/TurnTrace.json`은 30개 필드를 필수로 요구하므로(값은 null 허용) I6 수집이 이 예시를 `INVALID_RECORD`로 거절한다. ops-feedback 시험은 필수 필드를 모두 null로 채운 최소 trace를 자체 제작해 쓴다.

## 제안
예시의 `trace`를 스키마를 통과하는 최소 trace(필수 30필드 모두 존재, 대부분 null)로 바꾸고, `verify.py`에 "TraceEnvelope.trace를 해당 버전의 TurnTrace로 검증"하는 검사를 더한다(TraceEnvelope 스키마는 구조만 보므로 지금은 못 잡는다).

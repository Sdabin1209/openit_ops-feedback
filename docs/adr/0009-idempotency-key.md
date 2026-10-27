# 0009. 수집 멱등 키는 (session_id, turn_id)

- 상태: 채택(브리프 3-1절 "(session_id, turn_id)로 중복을 제거한다", D16 최소 1회 전달과 턴 ID 멱등).
- 맥락: survey-service가 최소 1회 전달로 보내므로 같은 Trace가 여러 번 온다.
- 결정: `UNIQUE(session_id, turn_id)`와 `INSERT ... ON CONFLICT DO NOTHING`으로 먼저 받은 것을 남기고 `DUPLICATE`를 돌려준다. 같은 키에 다른 내용이 와도 덮어쓰지 않는다(`envelope_hash`는 저장만 하고 비교하지 않는다).
- 왜: 계약이 키를 정했다(INTERFACES I6, FAULT_POLICY 원칙 2 "같은 turn_id는 멱등"). DB 유일 제약으로 하면 여러 프로세스가 동시에 보내도 한 건만 남는다(시험: 동시 8건 중 ACCEPTED 1).
- 대안: 봉투 해시를 키로 -> 같은 턴의 재전송이 조금 달라지면 이중 기록. 내용 불일치를 오류로 -> 계약(CONVENTIONS) 변경이 필요해 헤드 확인 대상(점검 D3)이라 하지 않았다.
- 결과: 얻는 것은 이중 기록 없음. 감수하는 것은 다른 내용의 재전송이 조용히 무시된다는 것. `minimize` 뒤에도 키가 남아 중복이 유지된다.
- 되돌리는 조건: 계약이 내용 불일치 신호를 정하면 따른다.

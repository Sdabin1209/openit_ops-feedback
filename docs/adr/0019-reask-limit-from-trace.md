# 0019. 되묻기 한도 정지는 trace의 pause_reason을 읽는다 (REASK_STREAK 재계산 폐기)

- 상태: 채택(헤드 결정, 2026-10-29). 0012 결정 1(REASK_STREAK 세는 방식과 threshold 3)을 대체한다(Supersedes 0012 결정 1). 0012 결정 2는 그대로다.
- 맥락: `REASK_STREAK`은 대화 로그에서 REASK·CONFIRM 연속 횟수를 다시 세어 threshold 이상이면 사례를 만들었다. 이는 survey-service `_reask_escalation`(graph.py)의 판단을 재구현한 것이다(구조 규칙 R7: 한 컴포넌트가 보증한 값을 다른 쪽이 다시 계산하지 않는다, 점검 OF-02). 실제로 한 칸 어긋나 있었다: 서비스는 이전 턴 3번 뒤 4번째에 정지하고, 우리는 이번 턴을 포함해 3번째에 걸렸다. 서비스의 상한(`SURVEY_MAX_CONSECUTIVE_REASK`, 문항 세트의 limit 중 최솟값)이 바뀌어도 우리는 모른다.
- 결정: 서비스가 정지를 판단해 TurnTrace에 `pause_reason = REASK_LIMIT`로 남긴다(계약 2.1.0). `REASK_STREAK`은 연속 횟수를 세지 않고 그 값을 읽어, 값이 `REASK_LIMIT`인 턴에서만 탐지한다. 값이 없는 trace에서는 탐지하지 않는다. 설정의 `params.threshold`·`actions`는 계약 `OpsFeedbackJobConfig`에 필수로 남아 있어 지우지 않고 무시한다(시작할 때 경고, 폐기 예정).
- 왜: 구조 규칙 R7과 한 사실은 아는 영역이 한 곳에서만 쓴다는 워크스페이스 규칙. 서비스가 정지한 그 턴에 사례가 생기고, 서비스 설정이 바뀌어도 어긋나지 않는다.
- 검토한 대안: (a) 서비스 정의를 복제해 한 칸 맞추기: 상한을 우리가 알 수 없어 다시 어긋난다. (b) 규칙을 삭제하기: `TURN_FAILURE_SIGNAL`이 같은 사건을 잡지만 신호 없이 `log_entry`에만 사유가 있는 경로(시험 `test_reask_limit_streak_without_signal_keeps_streak_detection`)와 설정·규칙 ID 호환이 깨진다.
- 결과: 얻는 것은 서비스와 같은 턴에 사례가 생기는 것과 재계산 코드 제거. 감수하는 것은 서비스가 pause_reason을 남기지 않은 옛 trace에서 되묻기 정체를 잡지 못하는 점, 그리고 비식별 정책이 `keep_pause_reason`을 꺼 두면 이 규칙이 아무것도 못 잡는 점(예시 설정은 켬).
- 되돌리는 조건: 서비스가 pause_reason을 trace에 남기지 않게 되거나, 정지 전 정체를 미리 보고 싶다는 요구가 생기면(그때는 서비스의 값을 읽는 별도 규칙으로 정의한다). 계약이 `threshold`를 선택으로 바꾸면 예시 설정에서 뺀다.

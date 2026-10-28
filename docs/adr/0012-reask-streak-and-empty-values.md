# 0012. REASK_STREAK 임계값 3, EXTRACT_FAILED는 HOLD·SKIP의 빈 값을 실패로 본다

- 상태: 채택(헤드 결정 G12/G14, 2026-10-28). 결정 1의 세는 방식은 같은 날 헤드 결정으로 바뀌었다(아래 "변경 이력"). 0005의 자리표시자 임계값을 대체한다(Supersedes 0005의 "임계값과 기본값의 근거" 중 REASK_STREAK 항목).
- 맥락: 예시 설정의 `REASK_STREAK`은 `enabled: false`, threshold 3이 자리표시자였다. `EXTRACT_FAILED`가 값 없는 HOLD·SKIP을 실패로 볼지도 미정이었다.

## 결정 1: REASK_STREAK 기본을 켜고 threshold 3, 반복 물음은 REASK·CONFIRM 합산 연속 턴 수
- 정의(현행): REASK 또는 CONFIRM이 연속으로 나온 턴 수(둘을 합쳐 센다, 문항은 보지 않는다). 그 밖의 action이나 추출 없는 턴이 나오면 리셋한다. COMMITTED가 아닌 턴은 건너뛴다. 이번 턴을 포함해 3 이상이면 걸린다. `actions` 기본 [REASK, CONFIRM].
- 왜: survey-service가 연속 REASK·CONFIRM 3번 뒤 4번째에 직원 호출 경로로 넘기도록 정했다(헤드 G12, survey-service ADR-0014 `SURVEY_MAX_CONSECUTIVE_REASK` 기본 3). 3의 출처는 표준 대화 설계의 '되묻기 2~3회 뒤 사람 연결'과 head FAULT_POLICY의 3회 재시도 관행이다. 이 규칙은 서비스가 사람을 부르기 직전 턴(3번째)부터 표시하고, 4번째(직원 호출로 넘어간 턴)도 연속 4로 계속 걸린다.
- **측정하지 않았다.** 3이 실제 문진에서 오탐·미탐이 적은 값인지 데이터로 보지 않았다(survey-service ADR-0014도 H1 실측 전 임시값이라 적는다).
- 정의를 서비스와 맞춘 이유: survey-service `graph.py`의 `_reask_escalation`은 REASK·CONFIRM을 합쳐 문항과 상관없이 세고 다른 action에서 리셋하며 COMMITTED가 아닌 턴은 건너뛴다. 정의가 다르면 같은 사건을 서비스와 탐지가 다르게 세어 운영자가 혼란스럽다(직원 호출은 됐는데 탐지는 안 걸리거나 그 반대). 이 규칙은 서비스가 사람을 부르기 직전 턴(3번째)부터 표시하고 4번째(직원 호출 턴)도 연속 4로 걸린다.
- 변경 이력: 처음 채택안은 같은 action·같은 `next_item`만 세는 것이었다(문항 단위 막힘이 목적). 헤드가 위 이유로 폐기하고 서비스 정의로 통일하도록 결정했다(2026-10-28). 서비스의 정의 자체가 바뀌면 이 규칙을 같이 바꾼다(서비스 문서가 정본). 한계: `next_item`을 보지 않으므로 서로 다른 문항을 오가는 되묻기도 연속으로 센다. 서비스도 그렇게 센다.
- 대안: (a) 2: 표준의 하한. 한 번 되묻고 다시 묻는 정상 정정까지 걸려 사례가 늘 것이다(추정, 측정 안 함). (b) 4 이상: 서비스가 이미 사람을 부른 뒤에야 표시된다. (c) 같은 action·같은 문항만 세기: 처음 안이었으나 서비스와 정의가 달라 폐기(변경 이력).
- 결과: 얻는 것은 되묻기 정체의 조기 표시. 감수하는 것은 값이 측정 전 값이라는 점.
- 되돌리는 조건: 운영 trace에서 오탐·미탐률을 측정하거나, survey-service가 상한 값을 바꾸면(그 영역 문서가 정본) 숫자만 설정에서 바꾼다.

## 결정 2: EXTRACT_FAILED는 값이 빈 HOLD·SKIP도 실패로 본다(기본 허용 action은 REDIRECT만, 코드 변경 없음)
- 읽은 근거(boundary-contracts, 읽기 전용):
  - `ActionExtraction.schema.json`: HOLD는 `reason: UNCERTAIN`과 `targets` 필수, SKIP은 `reason: REQUEST_SKIP|REFUSED`와 `targets` 필수. REDIRECT만 `targets: false`, `values: maxItems 0`이다.
  - V2_DRAFT 2.2: `targets`는 1~4개이며 "모든 target은 같은 행 values의 key". 따라서 HOLD·SKIP은 values가 비어 있을 수 없다. 건수는 HOLD 225, SKIP 167이고 값 없는 HOLD·SKIP은 문서에 없다.
- 판단: HOLD·SKIP은 정상 행동이지만 정상일 때는 항상 UNKNOWN·REJECTED 등의 값 칸을 가진다. 그 규칙은 값이 있는 HOLD·SKIP은 이미 실패로 보지 않는다. 값이 빈 HOLD·SKIP은 `targets`가 가리킬 칸이 없으므로 계약 위반이고, 이것이 이 규칙이 잡으려는 상태다. 그래서 허용 목록에 더하지 않는다.
- 시험으로 고정했다: `tests/test_detect.py`의 `test_hold_skip_with_values_is_normal_and_without_values_is_failure`.
- 한계: 이 영역은 학습 데이터나 운영 trace를 직접 보지 않았다(계약 문서와 스키마를 읽은 것).
- 예시 설정에서 켠다(`enabled: false` 제거).
- 되돌리는 조건: 계약이 값 없는 HOLD·SKIP을 허용하도록 바뀌면(boundary-contracts 변경) `empty_values_ok_actions`에 더한다.

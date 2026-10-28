# 0012. REASK_STREAK 임계값 3, EXTRACT_FAILED는 HOLD·SKIP의 빈 값을 실패로 본다

- 상태: 채택(헤드 결정 G12/G14, 2026-10-28). 0005의 자리표시자 임계값을 대체한다(Supersedes 0005의 "임계값과 기본값의 근거" 중 REASK_STREAK 항목).
- 맥락: 예시 설정의 `REASK_STREAK`은 `enabled: false`, threshold 3이 자리표시자였다. `EXTRACT_FAILED`가 값 없는 HOLD·SKIP을 실패로 볼지도 미정이었다.

## 결정 1: REASK_STREAK 기본을 켜고 threshold 3, actions [REASK, CONFIRM]
- 같은 문항(`next_item`)에서 같은 action이 이번 턴을 포함해 3번 연속이면 걸린다.
- 왜: survey-service가 연속 REASK·CONFIRM 3번 뒤 4번째에 직원 호출 경로로 넘기도록 정했다(헤드 G12, survey-service ADR-0014 `SURVEY_MAX_CONSECUTIVE_REASK` 기본 3). 3의 출처는 표준 대화 설계의 '되묻기 2~3회 뒤 사람 연결'과 head FAULT_POLICY의 3회 재시도 관행이다. 이 규칙은 서비스가 사람을 부르기 직전 턴(3번째)부터 표시하고, 4번째(직원 호출로 넘어간 턴)도 연속 4로 계속 걸린다.
- **측정하지 않았다.** 3이 실제 문진에서 오탐·미탐이 적은 값인지 데이터로 보지 않았다(survey-service ADR-0014도 H1 실측 전 임시값이라 적는다).
- 서비스와 다른 점(확인한 것, survey-service `graph.py`의 `_reask_escalation`): 서비스는 REASK와 CONFIRM을 합쳐 문항과 상관없이 센다. 이 규칙은 같은 action과 같은 `next_item`만 센다. 따라서 이 규칙이 거는 턴은 서비스도 센 턴의 부분집합이며 REASK 뒤 CONFIRM으로 섞이면 놓친다(미탐 쪽으로 보수적). `next_item`이 CONFIRM에서 대상 칸의 문항이라는 점(boundary-contracts V2_DRAFT 2.3, CONFIRM 333건 중 55건만 입력 문항과 같다)도 이 비교가 문항 단위 연속과 일치하지 않을 수 있는 이유다.
- 대안: (a) 2: 표준의 하한. 한 번 되묻고 다시 묻는 정상 정정까지 걸려 사례가 늘 것이다(추정, 측정 안 함). (b) 4 이상: 서비스가 이미 사람을 부른 뒤에야 표시된다. (c) 서비스와 같은 합산·문항 무관 세기: 문항 키가 다른 연속은 서로 다른 문제라 이 규칙의 목적(한 문항에서 막힘)과 달라 채택하지 않았다. 필요하면 별도 변경으로 한다.
- 결과: 얻는 것은 되묻기 정체의 조기 표시. 감수하는 것은 값이 측정 전 값이라는 점과 REASK·CONFIRM 혼합 연속의 미탐.
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

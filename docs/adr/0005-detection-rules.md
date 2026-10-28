# 0005. 실패 탐지는 구조 필드 규칙, 임계값은 설정

- 상태: 채택. REASK_STREAK 임계값은 [0012](0012-reask-streak-and-empty-values.md)가 대체했다(아래 임계값 항목은 당시 기록).
- 맥락: 어떤 턴을 실패로 볼지 정해야 한다. 의미적 오류까지는 규칙으로 못 잡는다.
- 결정: 비식별 뒤 trace의 구조 필드만 보는 규칙들(`detect.py`): `TURN_FAILURE_SIGNAL`, `TURN_FAILED_STEPS`, `COMMIT_ERROR`, `RESPONSE_FALLBACK`(원래 이름 GENERATOR_FALLBACK, ADR 0015), `STAFF_ALERT_DELIVERY_FAILED`, `INTERVENTION_FAILED`, `EXTRACT_FAILED`, `REASK_STREAK`. 켤 규칙과 값은 설정이 정한다.
- 왜: 규칙은 결정적이어서 같은 trace에 같은 결과를 낸다(수용 기준). 탐지기가 비식별 뒤 trace만 받는 구조라 원본이 필요한 탐지기를 만들 수 없다.
- 임계값과 기본값의 근거:
  - `REASK_STREAK.threshold`: 기본값 없음, 예시 설정의 3은 **자리표시자**다(사용자 결정 대기, README G14). 근거 기록 없음. 예시는 `enabled: false`.
  - `EXTRACT_FAILED.empty_values_ok_actions` 기본 `["REDIRECT"]`: survey-service design.md 18절에서 값이 없으면 REDIRECT라는 설명과 학습 데이터 5,077행 전수 일치라는 기록에 의존한다. 이 영역은 그 데이터를 직접 보지 않았다(확인: 그 문서만). 예시는 `enabled: false`.
  - 같은 `next_item`이 되묻는 문항을 가리키는지는 미확인(detect.py 주석).
- 대안: 모델 기반 탐지기 -> 후속(Detector 교체 지점은 열어 둠). 사람 신고 경로 -> 후속.
- 결과: 얻는 것은 재현 가능한 탐지. 감수하는 것은 의미적 오류를 놓치는 것.
- 되돌리는 조건: (G14 결정은 0012에서 반영됨.) 규칙 오탐·미탐이 측정되면(측정 안 함) 규칙을 조정한다.

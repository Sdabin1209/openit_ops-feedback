# 0014. 정지 사유 pause_reason을 사례·지표에 보존하고, 서비스가 이미 호출한 사건은 중복 사례로 만들지 않는다

- 상태: 채택(헤드 결정, boundary-contracts 2.1.0 = CR-H002, 2026-10-28). 0012(REASK_STREAK 정의)를 보완하고 0013의 "정책 지문이 바뀌어도 기존 사례는 재처리하지 않는다" 원칙을 따른다. 대체하는 ADR은 없다.
- 맥락: 위험 신호 정지와 되묻기 한도 정지는 둘 다 `PAUSED_RISK`이고 `turn_failure_signal`의 모양이 같았다. 전자는 환자 안전, 후자는 모델·질문 품질 문제라 대응이 다른데 이 영역은 한 덩어리로 셌다. 계약 2.1.0이 `TurnFailureSignal.pause_reason`(optional, `RISK|REASK_LIMIT|OTHER`, 모르는 값은 `OTHER`)을 더했다(문진 스키마는 survey-service 소유).

## 결정
1. **보존**: 비식별은 설정 `deid.keep_pause_reason: true`일 때 TurnFailureSignal(네 자리: `frontend_input`, `orchestrator_output`, `log_entry`, `orchestrator_output.log_entry`)의 `pause_reason`을 열거값으로 정규화해 남긴다. 모르는 값은 `OTHER`로 바꾸고 원문은 남기지 않는다. 끄면(기본) 스키마가 아는 값도 지운다.
2. **사례**: 사유가 있으면 `FailureCase.ext["ops-feedback"].pause_reason`에 적고 `TURN_FAILURE_SIGNAL` 탐지 detail에도 싣는다. 사유가 없으면(옛 trace, 정지가 아닌 턴, 정책이 끔) 아무것도 적지 않는다. 사유를 TurnTrace에서 추정하지 않는다(CR-H002가 추정안을 버린 이유: 정지를 결정한 서비스가 가장 정확히 안다).
3. **지표**: 롤백 평가 지표 스냅샷과 평가 결과에 `by_pause_reason`(사유가 기록된 사례만 센 건수)을 더한다. 실패율 계산과 롤백 기준은 바꾸지 않는다(기준은 사용자 결정).
4. **중복 방지**: 한 턴에서 `pause_reason == REASK_LIMIT`이고 `REASK_STREAK`과 `TURN_FAILURE_SIGNAL`이 함께 걸리면 둘을 하나로 합쳐 `TURN_FAILURE_SIGNAL` 탐지 하나로 낸다(detail에 `reask_streak`, `service_escalated: true`). 신호가 없는 턴에서 `REASK_STREAK`만 걸리면 그대로 두고 detail에 `service_escalated: true`, `pause_reason`을 단다. 사유가 `RISK`·`OTHER`이거나 없으면 합치지 않는다(서로 다른 원인).
5. **정책 지문**: `keep_pause_reason`을 켠 정책만 지문에 `keep_pause_reason: true`를 더한다. 쓰지 않는 기존 설정의 지문은 그대로다. 켜면 지문이 바뀌지만 이미 게시된 PENDING version 1 사례는 덮어쓰지 않고 `skipped_diff`로 센다(0013과 같은 원칙). 새 정책으로 다시 보려면 새 사례가 생기는 trace부터 적용되고, 기존 사례에 사유가 필요하면 사람이 지우고 `--reprocess`한다(자동 재처리 없음).

## 왜
- 열거 3값은 식별정보가 아니고 목적(위험 정지 대 품질 정지의 분리)에 필요한 최소 정보라 개인정보 최소 수집 원칙에 맞는다. 모르는 값을 `OTHER`로 읽는 것은 계약(PUBLIC_API.md H절)의 규약이다.
- 합치는 이유: 0012가 "서비스가 사람을 부르는 턴도 연속 4로 계속 걸린다"고 정했으므로 같은 턴이 신호와 반복 물음 두 탐지를 가진다. 둘은 한 사건이라, 목록과 지표에서 한 번만 보이게 한다. 3번째 턴(서비스 호출 직전)의 REASK_STREAK 사례는 조기 경고라는 목적이 달라 그대로 둔다.
- 재처리하지 않는 이유: 사례는 검토·이웃 영역 소비의 대상이라 불변이다(FailureCase 계약). 설정 변경으로 조용히 바뀌면 이웃이 다른 시점의 값을 본다.

## 검토한 대안과 버리는 이유
- 사유를 TurnTrace 다른 필드(응답 문장 RISK_NOTICE/CALL_STAFF)로 추정: 문구 변경에 깨지고 틀릴 수 있다(CR-H002 4절).
- 플래그 없이 항상 보존: 스키마 스냅샷을 동기화하는 것만으로 비식별 결과가 바뀌어 정책 지문이 설정과 어긋난다. 설정으로 명시해 지문과 일치시킨다.
- REASK_LIMIT 턴에서 REASK_STREAK를 아예 끔: 신호가 없는 경로(알림 저장 실패 등)에서 사건을 놓친다.
- 사유별로 실패율·롤백 기준을 따로 둠: 기준 값은 사용자 결정이고 측정이 없다. 분리 집계만 하고 기준은 만들지 않는다.

## 결과
- 얻는 것: 위험 정지와 되묻기 한도 정지를 사례와 지표에서 구분, 한 사건이 두 탐지로 세어지지 않음, 옛 trace 호환(필드 없음 = 사유 없음).
- 감수하는 것: ingest는 문진 스키마(TurnTrace)로 trace를 검증하는데 스키마의 `pause_reason`이 닫힌 열거라, 서비스가 나중에 새 값을 내면 수집이 INVALID_RECORD로 거부한다(읽기의 "모르는 값 = OTHER"는 스키마 갱신 전에는 수집 단계에서 못 쓴다). 이 영역이 고칠 수 없고 survey-service가 정한다. 측정한 값은 없다.
- 되돌리는 조건: 계약이 사유 열거를 바꾸거나 survey-service가 TurnFailureSignal에서 필드를 빼면 `keep_pause_reason`을 끄고 새 ADR로 대체한다.

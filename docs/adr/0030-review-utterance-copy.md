# 0030. 검토 사본: 환자 발화 두 칸만 원문으로, reviewer 전용 경로에서만 읽는다

- 상태: 채택(L12, 사용자 결정 2026-11-03). 계약 3.2.0(`OpsFeedbackReviewTrace`, `GET /v1/review-traces/{ref}`, `deid.review_utterance`)의 영역 쪽 구현 결정이다. 계약 쪽 결정은 헤드의 ADR 0016.
- 맥락: 비식별 trace는 자유 문자열을 모두 마스킹한다(ADR 0004). 발화가 마스킹되면 검토자가 실패 원인을 볼 수 없어 사람 검토(CONFIRMED)가 성립하지 않는다. 롤백 근거도 CONFIRMED만 센다(ADR 0029)라 검토가 곧 안전 장치다.
- 결정:
  - 정책 `deid.review_utterance.keep=true`일 때만 `frontend_message.utterance`, `extract_input.user_utterance` 두 칸(경로 지정, 키 이름 기준 아님)의 원문을 별도 **검토 사본**에 담는다. 비식별 trace(`trace`)와 `FailureCase`, trace ref(마스킹본의 해시)는 이전과 같다.
  - 검토 사본은 `failures.db`의 `aux`(kind `review_trace`, key=trace ref)에 둔다. 사례가 지워질 때(보존 정리, 세션 삭제) 함께 지운다. 수명은 사례(확정 사례 180일)와 같다.
  - 읽기는 `GET /v1/review-traces/{ref}` 하나, 검토 토큰(reviewer 역할)만. 읽기 토큰은 403. 신원 필수 설정이면 `X-Openit-User`가 있어야 한다. 읽기 전에 접근 기록(kind `trace_access`: 누가·역할·언제·어느 trace ref·원문 제공 여부)을 쓰고, 쓰지 못하면 내보내지 않는다(fail-closed). 기록 보존은 `metrics_days`(기본 365일, 감사 기록이라 사례 180일과 별도).
  - 원문 칸에도 잔존 패턴(설정의 `residual_patterns`: 전화·주민번호·이메일)을 같은 방식으로 검사한다. 걸리면 `DeidError`로 사례를 만들지 않는다(`DEID_FAILED`).
  - 발화가 `max_chars`(최대 1000)를 넘으면 앞부분만 남기고, 검사도 남기는 부분에 한다.
  - `keep=false`(기본)이면 사본을 만들지 않고, 검토 경로는 마스킹된 trace를 낸다(접근 기록은 남는다).
  - `POST /v1/cases/{id}/review`의 `reviewed_input`은 읽기 역할 전체가 읽는 `FailureCase`에 실린다. 보존한 발화 원문이 그대로(완전히 같거나 10자 이상이면 포함) 들어 있으면 `INVALID_RECORD`로 거부한다. 바꿔 쓴 문장은 막지 못한다: `reviewed_input`은 검토자가 개인정보 없이 직접 쓰는 입력이라는 계약 의미를 그대로 둔다(10자라는 값은 측정하지 않은 보수 값).
- 왜: 사용자 결정. 최소 노출 원칙을 지키려고 (1) 원문을 두 칸으로, (2) 읽는 역할을 하나로, (3) 읽기마다 기록, (4) 수명을 사례와 같게 묶었다. 사본을 `trace`와 분리해 두어 기존 소비자(합성 데이터, model-lab, 문항 저작)가 받는 모양이 바뀌지 않는다.
- 검토한 대안과 버리는 이유: (1) `keep_string_keys`에 `utterance` 키 추가 -> 같은 이름의 모든 칸(예: `log_entry.input.utterance`)이 모든 읽기 역할에게 나간다. (2) 비식별 trace 자체에 원문 -> 읽기 역할 전체에 노출. (3) 별도 테이블/마이그레이션 -> `aux`로 충분하고 스키마 변경 없이 같은 보존·삭제 경로를 쓴다(대신 `aux`의 kind 열거가 늘었다). (4) `reviewed_input`을 마스킹본 스키마로 검증 -> `reviewed_input`은 trace가 아니라 모델 입력(ActionExtractionInput, ResponseRequest)이라 모양이 다르고, LoRA-A 입력의 `user_utterance`는 필수 자유 문장이라 마스킹 토큰만 허용하면 입력으로서 쓸모가 없다.
- 결과: 얻는 것은 검토 가능성과 기존 소비자 호환. 감수하는 것은 (a) 가명 정보가 아닌 원문 발화가 `failures.db`에 최대 180일(확정 사례 기준) 남는다는 점, (b) DB 백업에도 담긴다는 점(백업 보존은 deploy), (c) 정책 지문이 바뀌어 기존 PENDING 사례는 `skipped_diff`가 아니라 같은 사례로 인정되지만 사본이 없다. `--reprocess`로 다시 돌리면 사례는 그대로 두고 사본만 채운다(원본 `log.db`가 남아 있는 범위), (d) 접근 기록은 trace ref 단위이며 사례 ID로 바로 찾으려면 사례의 `deidentified_trace_ref`로 대조한다.
- 되돌리는 조건: `keep=false`로 끄면 새 사본은 만들지 않는다. 이미 만든 사본은 사례 보존 정리 또는 `admin delete-session`으로 지운다.

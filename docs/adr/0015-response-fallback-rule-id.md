# 0015. 규칙 ID GENERATOR_FALLBACK을 RESPONSE_FALLBACK으로 바꾼다

- 상태: 채택(헤드 지시, 2026-10-28). 0005의 규칙 목록 중 한 이름을 바꾼다.
- 맥락: `GENERATOR_FALLBACK`은 v1 trace의 `generator_output.is_fallback`에서 온 이름이다. 현행 trace는 응답 생성기 대신 LoRA-B 응답 단계를 쓰고, 규칙은 `response_outcome == "fallback"`(서버 대체 문장으로 끝남)을 본다. 이름이 없어진 구성요소를 가리켰다. 뜻이 같아 "옛 이름 유지"로 두었으나 현행 용어와 어긋난다.
- 결정: 규칙 ID를 `RESPONSE_FALLBACK`으로 바꾼다. 동작은 같다. 옛 이름은 `RETIRED_RULES`에 넣어 설정에 있으면 대체 이름을 알리는 오류로 시작을 거부한다(`PARSER_EMPTY`와 같은 방식).
- 왜: 사실(용어)을 한 곳의 현행 trace 필드 이름에 맞춘다. 조용한 별칭은 두 이름이 오래 공존해 어느 쪽이 정본인지 흐려진다. 거부하면 deploy 설정이 안 바뀐 채 배포되는 일이 시작 때 드러난다.
- 대안: (a) 옛 이름 유지: 용어 어긋남이 남는다. (b) 두 이름 모두 받는 별칭: 위 이유로 버린다.
- 결과: 얻는 것은 용어 일치. 감수하는 것은 (1) 설정(`config/job.example.yaml`, deploy의 `ops-feedback-job.yaml`)과 `classification.rule_to_type`·`priority`의 이름을 함께 바꿔야 하는 것, (2) 규칙 ID가 정책(탐지·분류) 지문에 들어가므로 이미 게시된 FailureCase와 같은 입력도 새 설정에서는 규칙 ID와 지문이 달라지는 것. 처리는 ADR 0013과 같다: 기존 사례는 덮어쓰지 않고 다시 처리하지 않으며, 잡이 같은 사례를 다시 만나면 결과가 달라진 것으로 보고 `skipped_diff`로 센다(`job.py`). 새 사례만 새 규칙 ID로 쌓인다. 이 처리를 측정하지는 않았고 코드를 읽어 확인했다.
- 되돌리는 조건: 이름 변경으로 얻는 것보다 기존 사례와의 불연속 비용이 크다고 판명되면 새 ADR로 대체한다.

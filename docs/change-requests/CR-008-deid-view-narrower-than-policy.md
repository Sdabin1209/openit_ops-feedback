# CR-008: 비식별 뷰 계약이 정책 선택지보다 좁다 (OpsFeedbackDeidentifiedTrace)

상태: 제안(헤드 결정 필요). 작성: ops-feedback, 2026-10-29. 근거: 단위 3에서 비식별 출력을 `OpsFeedbackDeidentifiedTrace`로 자기 검사하게 했다.

## 사실 (직접 돌려 확인한 것)

계약 스키마는 자유 문자열 칸의 값을 `[MASKED]|[DROPPED]`(Redacted), 식별자 칸을 `Ident`(64자 이하 식별자 모양 또는 `[MASKED]`), 건수 맵의 키를 `^[A-Z][A-Z0-9_]{0,31}$`로 정한다. 코드가 지원하던 아래 정책 선택지는 이 모양을 벗어난 출력을 만든다. 자기 검사를 넣자 그 시험들이 `DeidError`로 닫혔다(시험은 닫힘을 기대하도록 고쳤다).

| 정책 선택지 | 출력 | 계약과 어긋나는 곳 |
| --- | --- | --- |
| `scrub_string_keys` + `scrub_patterns` | `... [PHONE] ...` 같은 치환된 문장 | Redacted는 `[MASKED]\|[DROPPED]`만 |
| `unlisted_string_action: drop` | 문자열 칸의 키 제거 | 필수 칸(`response_output` 등)이 빠진다 |
| `count_map_key_pattern` 소문자 등 | 소문자 키 | 계약 키 패턴과 다름 |
| `keep_string_patterns`로 느슨하게 둔 keep 값(`.*`) | 64자 넘는 값 | `Ident`는 64자 이하 |
| 스키마에 없는 키가 든 가지 | 가지 통째 제거 | 최상위 필수 키가 빠진다 |

null `extract_output.values[].value`는 계약이 `[MASKED]|[DROPPED]`만 허용하므로 코드를 고쳐 null도 `[MASKED]`로 가린다(코드 쪽 수정, 요청 아님).

## 요청 (둘 중 하나, 결정은 헤드)

1. 계약을 넓힌다: Redacted에 치환 문장을 허용하는 변형을 두고 drop을 허용하는 칸을 optional로, 건수 키 패턴과 Ident 길이를 정책에서 정하게 한다. 소비자(admin-console, model-lab)가 마스킹 전제로 읽는지는 이 영역이 알 수 없다(미확인).
2. 좁은 계약을 정본으로 두고 이 영역이 위 선택지를 설정 스키마(`OpsFeedbackJobConfig`)에서 뺀다. 그 경우 `OpsFeedbackJobConfig`의 `Deid`가 `scrub_*`, `unlisted_string_action: drop`을 허용하는 것도 함께 바꿔야 한다.

그 전까지 이 영역은 계약을 따른다(어긋나는 정책으로 만든 턴은 `DEID_FAILED`).

# 0023. 비식별 설정에서 scrub·drop·count_map_key_pattern 선택지를 없앤다

- 상태: 채택(헤드 결정 U25, 계약 boundary-contracts 3.0.0 항목 5). [0004](0004-deid-allowlist.md)·[0013](0013-extract-protections-counts.md)의 해당 설정을 대체한다. 변경 요청 CR-008의 "계약 쪽을 좁히지 말고 영역이 선택지를 뺀다" 안.
- 맥락: 비식별 뷰 계약(`OpsFeedbackDeidentifiedTrace`)은 자유 문자열을 `[MASKED]`로, 건수 맵 키를 `^[A-Z][A-Z0-9_]{0,31}$`로 정한다. `scrub_string_keys`·`scrub_patterns`(치환 문장), `unlisted_string_action: drop`(칸 제거), `count_map_key_pattern`(다른 키 모양)은 이 모양을 벗어난 출력을 내서 자기 검사에서 `DEID_FAILED`로 닫혔다(CR-008).
- 결정: 계약 3.0.0의 `OpsFeedbackJobConfig.deid`에서 세 선택지가 빠졌다. 코드도 같이 지웠다: scrub 치환 경로, drop 분기, 설정 가능한 건수 키 패턴(계약 패턴 상수 하나). `unlisted_string_action`은 키가 필수로 남지만 값은 `mask`만 받는다. 옛 설정 키가 정책에 들어오면 이유를 말하며 거부한다(스키마 검증을 거치지 않는 호출도 막는다).
- 왜: 계약 밖 출력을 만드는 선택지는 항상 실패로 끝나는 설정이었다. 지원하지 않는 것을 설정으로 열어 두면 오류가 운영 중에 드러난다(R7, 구조 규칙 6).
- 검토한 대안: 계약을 넓혀 치환 문장·키 제거를 허용(소비자 admin-console·model-lab이 마스킹 전제로 읽는지 이 영역이 알 수 없다), 현상 유지.
- 결과: 얻는 것은 설정과 출력의 일치와 코드 감소. 감수하는 것은 정책 지문이 한 번 바뀐다는 점(지문 입력에서 scrub·unlisted·패턴 항목을 뺐다): 이미 게시된 PENDING 사례는 재처리하면 `skipped_diff`로 센다. 운영 데이터는 아직 없다(2026-10-30).
- 되돌리는 조건: 소비자가 치환 문장이나 칸 제거를 요구하면 계약에 먼저 올린 뒤 이 영역이 따른다.

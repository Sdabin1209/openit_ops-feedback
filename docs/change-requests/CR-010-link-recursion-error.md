# CR-010: openit_link v0.1.0이 깊게 중첩된 JSON 본문에서 500을 낸다

- 영역: ops-feedback. 대상: `openit_link.server.App.handle`(link v0.1.0, 헤드 소유).
- 무엇이 문제인가: 본문이 `[[[...]]]`처럼 10만 단계로 중첩되면 `json.loads`가 `RecursionError`를 내는데, `App.handle`은 `ValueError`만 잡아 예외가 waitress까지 올라가 500이 된다. 전환 전 ops-feedback은 `RecursionError`도 잡아 400 INVALID_RECORD로 답했다(시험 `test_deeply_nested_json_and_non_ascii_token_do_not_drop_the_connection`).
- 제안: `json.loads` 예외 처리에 `RecursionError`를 더해 `BoundaryError("INVALID_RECORD", "본문이 JSON이 아니다")`로 바꾼다.
- 임시 처치: 영역 코드에서 우회하지 않는다(라이브러리의 몫). 그 시험은 `xfail`로 두었고 수정이 나오면 풀린다.

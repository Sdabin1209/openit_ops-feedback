# 시험의 시간 (U422)

시험 중 실제 시간을 기다리거나 재서 판정하는 곳은 0이다. `tests/test_no_real_time.py`가 시험 코드의 `time.monotonic/perf_counter/time.time/time.sleep`, 숫자 timeout, `.wait(숫자)`, `.alarm`을 막고,
동작 코드에서 시계·타이머를 직접 부르는 곳도 막는다(주입, [ADR 0027](../docs/adr/0027-inject-clock-and-wait.md)). 매달림 감시는 `tests/waiting.py`의 `HANG_GUARD_SECONDS = 300` 하나다.
처리: **지움** / **1**(우리 코드의 시간: 시계·대기 주입 + 가짜) / **2**(라이브러리 안의 시간: 넘긴 값 확인, 가짜 의존이 즉시 오류) / **3**(속도 측정: 시간 단언 삭제, 결과 단언만) / **매달림 감시**.

| 시험 (목록 u422-timing-tests.txt 및 직접 훑은 것) | 목적 한 줄 | 처리 | 바뀐 시험 |
|---|---|---|---|
| `test_server_properties::test_stuck_clients_do_not_block_other_requests` | 느린 연결이 다른 요청을 막지 않는다(waitress 동작) | 지움, 2 | `test_waitress_receives_our_limits`(일꾼 수 = 연결 상한) |
| `::test_stuck_connection_is_closed_by_the_timeout` | 멈춘 연결을 timeout에 닫는다(waitress) | 지움, 2 | `test_make_server_passes_the_request_timeout_and_connection_cap`(channel_timeout 값) |
| `::test_shutdown_finishes_the_request_in_flight` (목록 27) | 종료가 진행 중 요청을 기다린다 | 1+2 | `test_shutdown_stops_listening_then_lets_in_flight_requests_finish_then_closes`(호출 순서·인자), `test_shutdown_flush_loop_ends_when_the_injected_clock_passes_its_limit`(가짜 시계·대기) |
| `::test_connections_over_the_cap_wait_instead_of_spawning_workers` | 연결 상한 초과는 대기(waitress) | 지움, 2 | `test_waitress_receives_our_limits`(connection_limit = N+2) |
| `::test_oversized_ingest_body_is_rejected_fast` | 큰 본문 거부 | 2 | `test_ingest_surface_refuses_a_body_over_its_limit_without_reading_it`(우리 상한 값이 Route에 넘어갔는가, 소켓 없음) |
| (새) readyz 캐시 | 같은 창 안의 /readyz는 저장소를 다시 확인하지 않는다 | 1 | `test_readyz_result_is_shared_for_the_cache_window_by_the_injected_clock` |
| (새) 기동·한 번 왕복 | 서버가 뜨고 /healthz에 답하고 멈춘다 | 매달림 감시 | `test_one_real_round_trip_starts_answers_and_stops` |
| `test_audit_fixes::test_all_other_regexes_are_linear_on_the_same_inputs` (목록 21) | 비식별·검토 정규식 폭주 방지 | 3 | `test_all_other_regexes_run_on_the_same_inputs_without_error`(시간 단언 삭제, 오류 없이 끝남) |
| `::test_review_pii_scan_is_linear_for_hostile_inputs` | 검토 개인정보 검사의 선형성 | 3 | `test_review_pii_scan_gives_the_right_answer_on_hostile_inputs`(맞는 답만, 폭주 방어는 스키마 maxLength 20,000자 `test_review_field_length_cap_and_nesting`) |
| `::test_deid_scans_long_strings_in_windows_instead_of_rejecting_them` | 긴 문자열 창 검사가 개인정보를 잡는다 | 3 | 같은 이름(시간 단언만 삭제) |
| `::test_ingest_of_a_huge_free_text_field_is_fast` | 큰 자유 문자열 수집 | 3 | `test_ingest_of_a_huge_free_text_field_finishes`(시간 단언 삭제) |
| `::test_review_http_rejects_huge_and_slow_bodies_quickly` | 검토 면이 큰 본문을 거부 | 2 | `test_review_surface_refuses_a_body_over_its_limit_without_reading_it`(우리 상한 값, WSGI 직접 호출) |
| `::test_deeply_nested_json_and_non_ascii_token_do_not_drop_the_connection` | openit_link·waitress의 JSON 깊이·헤더 처리 | 지움 | 라이브러리(link) 시험의 몫 |
| `::test_sigterm_stops_the_server_cleanly` | SIGTERM에 우리 핸들러가 깨끗이 끝낸다 | 매달림 감시 | 같은 이름(기동을 로그 줄 사건으로 안다, 폴링·sleep 없음) |
| `test_postgres_timeout::test_readyz_503_within_probe_limit_and_recovers` (목록 22) | DB 무응답에서 /readyz가 503 | 지움, 2+1 | `test_probe_failure_is_unavailable_with_class_name_only`, `test_probe_remaining_time_comes_from_the_injected_clock`(남은 시간 = 상한 - 접속 시간) |
| `::test_readyz_not_blocked_by_main_connection_lock` (23) | /readyz가 주 연결 잠금을 쓰지 않는다 | 지움 | ping은 새 연결을 연다: `test_probe_remaining_time_comes_from_the_injected_clock`가 새 연결(`psycopg.connect`) 경로를 쓴다 |
| `::test_request_path_is_bounded_and_returns_unavailable` (24) | DB 무응답이면 요청 경로가 UNAVAILABLE | 지움, 2 | `test_statement_timeout_from_the_server_is_unavailable_and_not_retried`, `test_unrecoverable_loss_is_unavailable_and_hides_the_url`, `test_each_call_arms_an_abort_timer_of_statement_timeout_plus_one_second_and_disarms_it` |
| `::test_job_exits_nonzero_when_database_frozen` (25) | DB가 얼면 잡 종료 코드 2 | 지움, 2 | `test_connect_failure_is_unavailable_with_class_name_only`(접속 실패 -> UNAVAILABLE), `test_job_exits_with_code_2_and_prints_only_the_code_when_the_database_is_unavailable`(종료 코드 2) |
| `::test_read_and_review_are_bounded_when_database_frozen_and_review_is_retry_safe` (26) | 읽기·검토 면의 DB 무응답 503, 검토 재전송 안전 | 지움, 2 | 위 가짜 연결 시험 + 검토 멱등은 `test_api_review.py`(같은 검토 재전송 200) |
| `::test_invalid_timeout_setting_is_rejected` | 잘못된 시한 설정 거부 | 우리 값 | `test_invalid_timeout_setting_is_rejected`(여러 값) |
| (새) 넘긴 값 | 연결 파라미터에 statement_timeout·connect_timeout이 실린다 | 2 | `test_connection_parameters_carry_statement_and_connect_timeouts_and_keep_search_path`, `test_default_limits_and_env_override` |
| `test_postgres_reconnect.py` 전체 | 끊긴 연결을 다시 열고 한 번 재시도 | 지움, 2 | `test_lost_connection_is_reopened_and_the_call_retried_once`, `test_sql_errors_are_not_retried` |
| `test_upstream_faults::test_gate_fault_is_bounded_visible_and_retried_with_same_request` (28) | 게이트 오류가 보이고 같은 request_id로 재시도 | 2 | `..._is_visible_and_retried_with_same_request`(시간 단언 삭제, `hang` 사례는 `drop`: 응답 없이 연결 끊김으로 즉시 같은 '응답 못 받음') |
| `::test_release_read_fault_is_bounded_visible_and_does_not_lose_cases` (29) | 릴리스 읽기 오류가 보이고 사례를 잃지 않는다 | 2 | `..._is_visible_and_does_not_lose_cases`(위와 같다) |
| `::test_rollback_evaluation_has_overall_deadline` (30) | 롤백 평가 전체 상한 | 1 | `test_rollback_deadline_is_passed_to_the_injected_wait_and_expiry_is_reported`(상한 값이 wait에 넘어가고 '지남'이면 DEADLINE_EXCEEDED) |
| `::test_slow_gate_within_timeout_succeeds` | 클라이언트 timeout 안의 느린 응답 | 지움 | 라이브러리(boundary_contracts 클라이언트) 몫 |
| `test_build_image::test_real_build_*` | 이미지 라벨·재현성 | (시간 아님) | 고정 태그를 프로세스별 고유 태그로 |
| `test_secrets`, `test_postgres_select` 서버 기동 시험 | 비밀이 출력에 없다, 파일 토큰이 동작한다 | 매달림 감시 | 기동을 폴링·sleep이 아니라 `listening on` 로그 줄 사건으로 안다(`tests/waiting.py::Proc`) |
| `test_api`, `test_http_401`, `test_contract_schemas`, `test_secrets` 등의 숫자 timeout | 시한이 결과를 바꾸지 않는 호출 | 매달림 감시 | 숫자를 `HANG_GUARD_SECONDS`로 통일 |

## 보고할 것: 영역 시험에서 지워 smoke·E2E가 봐야 하는 것
- 실제 waitress가 느린 연결을 `channel_timeout`에 끊는지, 연결 상한 초과가 대기열이 되는지, 종료가 진행 중 요청을 마치는지(실제 소켓).
- 실제 PostgreSQL이 무응답일 때 `/readyz`·수집·읽기·검토가 상한 안에 503을 내고, 같은 검토 재전송이 한 번만 반영되는지, 잡이 종료 코드 2로 끝나는지(docker pause).
- 실제 느린 게이트 응답과 멈춘 게이트에서 클라이언트 timeout이 발동하는지.

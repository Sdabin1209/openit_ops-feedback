"""RUNTIME 조항 시험: R8.1(503 로그에 예외 종류·위치), R10.3(계약 클라이언트 timeout 30초), R10.5(로그 쓰기 실패가 요청을 실패시키지 않음),
R3.10·R4.1(PostgreSQL: 쓰기 불가면 /readyz 503·쓰기 503, 연결이 끊겨도 재시작 없이 복구)."""
from __future__ import annotations

import logging
import os

import pytest
from boundary_contracts.errors import BoundaryError

import ops_feedback.components.link_ingest_api as api
from conftest import _pg_schema_url, envelope, make_trace
from ops_feedback.wiring.build import CLIENT_TIMEOUT_SECONDS, _client_args
from ops_feedback.wiring.log_store import LogStore


def test_r10_3_client_timeout_defaults_to_30_and_config_wins():
    assert CLIENT_TIMEOUT_SECONDS == 30.0
    assert _client_args({"base_url": "https://x"})["timeout"] == 30.0
    assert _client_args({"base_url": "https://x", "timeout": 5})["timeout"] == 5


def test_r8_1_unavailable_logs_exception_kind_and_location_not_message(caplog):
    def boom():
        try:
            raise ValueError("SECRET-PATIENT-TEXT")
        except ValueError:
            raise BoundaryError("UNAVAILABLE", "x") from None
    with caplog.at_level(logging.ERROR, logger="ingest-api"):
        try:
            boom()
        except BoundaryError as e:
            api._log_unavailable(e)
    text = caplog.text
    assert "ValueError" in text and "test_conform_runtime.py:" in text and "SECRET-PATIENT-TEXT" not in text


def test_r10_5_log_write_failure_does_not_fail_the_request():
    class Full:
        def write(self, *a):
            raise OSError("disk full")
        def flush(self):
            raise OSError("disk full")
    h = logging.StreamHandler(Full())
    lg = logging.getLogger("ingest-api")
    lg.addHandler(h)
    old = logging.raiseExceptions
    logging.raiseExceptions = False
    try:
        lg.warning("x")           # 핸들러가 실패해도 호출한 쪽에는 예외가 오지 않는다
        lg.info("y")
    finally:
        logging.raiseExceptions = old
        lg.removeHandler(h)


def _owner_conn(request):
    import psycopg
    return psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True, options="-c default_transaction_read_only=off")


def test_r4_1_killed_connection_is_reopened_without_restart(request):
    import psycopg
    store = LogStore.postgres(_pg_schema_url(request))
    assert store.send(envelope(make_trace("s-k", "t-1")))["status"] == "ACCEPTED"
    with _owner_conn(request) as c:                      # 서버가 앱 연결을 끊는다(PostgreSQL 재시작과 같은 효과)
        c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND datname = current_database() AND application_name = ''")
    assert store.send(envelope(make_trace("s-k", "t-2")))["status"] == "ACCEPTED"      # 재시작 없이 성공
    store.ping()                                                       # /readyz 확인도 통과


def test_r3_10_read_only_database_is_not_ready_and_writes_are_unavailable(request):
    import psycopg
    url = _pg_schema_url(request)
    store = LogStore.postgres(url)
    db = psycopg.conninfo.conninfo_to_dict(os.environ["TEST_DATABASE_URL"])["dbname"]
    with _owner_conn(request) as c:
        c.execute(f'ALTER DATABASE "{db}" SET default_transaction_read_only = on')
        c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND datname = current_database()")
    try:
        for ping in (store.ping,):
            with pytest.raises(BoundaryError) as e:
                ping()
            assert e.value.code == "UNAVAILABLE"
        with pytest.raises(BoundaryError) as e:
            store.send(envelope(make_trace("s-ro", "t-1")))
        assert e.value.code == "UNAVAILABLE"
    finally:
        with _owner_conn(request) as c:
            c.execute(f'ALTER DATABASE "{db}" RESET default_transaction_read_only')
            c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND datname = current_database()")
    assert store.send(envelope(make_trace("s-ro", "t-2")))["status"] == "ACCEPTED"      # 복구되면 재시작 없이 다시 쓴다

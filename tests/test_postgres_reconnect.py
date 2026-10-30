"""PostgreSQL 연결이 끊겨도(서버 재시작, 연결 강제 종료) 서비스 재시작 없이 복구한다. TEST_DATABASE_URL이 없으면 SKIP."""
import json
import socket
import subprocess
import sys
import time
import urllib.request

import psycopg
import pytest

from boundary_contracts.errors import BoundaryError
from conftest import _pg_schema_url, envelope, make_trace
from ops_feedback.components.failure_case_store import FailureCaseStore as FailureStoreImpl
from ops_feedback.wiring.log_store import LogStore
from test_conformance_failurestore import TRACE, case
from test_postgres_select import _file, _scrub, _secrets_of


def _kill(*stores_conn):
    """주어진 연결들의 서버 쪽 세션을 강제로 끊는다(서버 재시작과 같은 결과: 클라이언트 연결이 죽는다)."""
    import os
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as c:
        for db in stores_conn:
            c.execute("SELECT pg_terminate_backend(%s)", (db.info.backend_pid,))
    time.sleep(0.2)


def test_log_store_recovers(request):
    log = LogStore.postgres(_pg_schema_url(request))
    assert log.send(envelope(make_trace("s-1", "t-1")))["status"] == "ACCEPTED"
    _kill(log._raw._backend._db)
    assert log.send(envelope(make_trace("s-1", "t-2")))["status"] == "ACCEPTED"      # 끊긴 뒤 첫 호출이 복구한다
    _kill(log._raw._backend._db)
    assert log.send(envelope(make_trace("s-1", "t-2")))["status"] == "DUPLICATE"
    _kill(log._raw._backend._db)
    assert len(log.grant_deidentifier_view().read_unprocessed()) == 2
    assert log.counts_by_release() == {"rel-0002": 2}
    _kill(log._raw._backend._db)
    log.ping()


def test_failure_store_recovers(request):
    fs = FailureStoreImpl.postgres(_pg_schema_url(request))
    fs.put_trace("tr-1", TRACE)
    _kill(fs._b._db)
    fs.publish_case(case())
    _kill(fs._b._db)
    assert fs.get_case("fc-1")["version"] == 1
    _kill(fs._b._db)
    fs.publish_case(case(version=2, review_status="CONFIRMED", expected={"items": []}))
    assert [c["version"] for c in fs.list_cases(latest_only=False)[0]] == [1, 2]


def test_unrecoverable_is_unavailable_and_hides_url(request):
    log = LogStore.postgres(_pg_schema_url(request))
    log.ping()
    log._raw._backend._url = "postgresql://svc:pw-canary-77@127.0.0.1:1/db"       # 다시 열 수 없는 주소
    _kill(log._raw._backend._db)
    with pytest.raises(BoundaryError) as e:
        log.send(envelope(make_trace("s-1", "t-1")))
    assert e.value.code == "UNAVAILABLE" and e.value.retryable
    assert "pw-canary-77" not in str(e.value) and "svc" not in str(e.value) and "127.0.0.1" not in str(e.value)


def test_sql_error_not_retried_as_connection_loss(request):
    fs = FailureStoreImpl.postgres(_pg_schema_url(request))
    with pytest.raises(BoundaryError) as e:
        fs._b._x(lambda: fs._b._db.execute("SELECT * FROM no_such_table"))
    assert e.value.code == "UNAVAILABLE"


def test_ingest_api_survives_backend_kill(tmp_path, request):
    import os
    log_url, fail_url = _pg_schema_url(request), _pg_schema_url(request)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = _scrub(OPS_INGEST_TOKEN="tok", OPS_READ_TOKENS="reader=rt", OPS_PORT=str(port), OPS_READYZ_CACHE_SECONDS="0",
                 OPS_LOG_DATABASE_URL_FILE=_file(tmp_path, "lu", log_url), OPS_FAILURE_DATABASE_URL_FILE=_file(tmp_path, "fu", fail_url))
    p = subprocess.Popen([sys.executable, "-m", "ops_feedback.api"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    def get(path):
        return json.loads(urllib.request.urlopen(base + path, timeout=5).read())
    def post(tid):
        rq = urllib.request.Request(base + "/v1/traces", data=json.dumps(envelope(make_trace("s-9", tid))).encode(),
                                    headers={"Authorization": "Bearer tok", "Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(rq, timeout=5).read())["status"]
    try:
        for _ in range(60):
            try:
                get("/readyz"); break
            except Exception:
                time.sleep(0.5)
        else:
            pytest.fail("readyz 응답 없음")
        assert post("t-1") == "ACCEPTED"
        # 이 데이터베이스의 다른 모든 세션을 끊는다(서비스의 연결 포함)
        with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as c:
            c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()")
        time.sleep(0.3)
        assert get("/readyz")["status"] == "ready"
        assert post("t-1") == "DUPLICATE" and post("t-2") == "ACCEPTED"
    finally:
        p.terminate()
        out = p.communicate(timeout=10)[0]
    for sec in _secrets_of(log_url):
        assert sec not in out

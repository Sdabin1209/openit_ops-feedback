"""저장소 선택(URL 있으면 PostgreSQL, 없으면 SQLite)과 접속 문자열 비노출. 서버(ingest-api)와 잡(run-once) 둘 다 본다.
PostgreSQL이 필요한 시험은 TEST_DATABASE_URL이 없으면 SKIP(PASS로 세지 않는다)."""
from waiting import HANG_GUARD_SECONDS, Proc
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit

import pytest
import yaml

from boundary_contracts.errors import BoundaryError
from conftest import _pg_schema_url, base_config, envelope, make_trace, pii_trace
from ops_feedback.wiring.storage import open_failure_store, open_log_store

CANARY = "pw-canary-9d41"
DEAD_URL = f"postgresql://svc:{CANARY}@127.0.0.1:1/db"      # 아무도 듣지 않는 포트


def _scrub(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPS_")}
    env.update(extra)
    return env


def _file(tmp_path, name, value):
    f = tmp_path / name
    f.write_text(value + "\n")
    return str(f)


def _secrets_of(url):
    u = urlsplit(url)
    return [x for x in (u.password, u.username, str(u.port)) if x]


def test_without_url_sqlite_is_used(tmp_path):
    log = open_log_store({}, str(tmp_path / "l.db"))
    fail = open_failure_store({}, str(tmp_path / "f.db"))
    assert log.backend_kind() == "sqlite" and fail.backend_kind() == "sqlite"
    assert open_failure_store({}, None) is None


def test_unreachable_postgres_error_hides_url(tmp_path):
    for env in ({"OPS_LOG_DATABASE_URL": DEAD_URL}, {"OPS_LOG_DATABASE_URL_FILE": _file(tmp_path, "u", DEAD_URL)}):
        with pytest.raises(BoundaryError) as e:
            open_log_store(env, "unused.db")
        assert CANARY not in str(e.value) and "svc" not in str(e.value) and "127.0.0.1" not in str(e.value)
        assert e.value.__cause__ is None or CANARY not in repr(e.value.__cause__)


def test_api_with_dead_postgres_exits_without_leaking(tmp_path):
    env = _scrub(OPS_INGEST_TOKEN="t", OPS_LOG_DATABASE_URL_FILE=_file(tmp_path, "u", DEAD_URL))
    r = subprocess.run([sys.executable, "-m", "ops_feedback.api"], env=env, capture_output=True, text=True, timeout=HANG_GUARD_SECONDS)      # 매달림 감시
    assert r.returncode == 2
    assert CANARY not in r.stdout + r.stderr and "svc" not in r.stdout + r.stderr


def test_file_wins_over_env_for_url(tmp_path):
    env = {"OPS_LOG_DATABASE_URL_FILE": _file(tmp_path, "u", DEAD_URL), "OPS_LOG_DATABASE_URL": "postgresql://x@127.0.0.1:2/y"}
    with pytest.raises(BoundaryError) as e:
        open_log_store(env, "unused.db")
    assert e.value.code == "UNAVAILABLE"


def test_job_run_once_on_postgres(tmp_path, request):
    log_url, fail_url = _pg_schema_url(request), _pg_schema_url(request)
    cfg = base_config()
    cfg["storage"] = {"log_db": str(tmp_path / "unused-log.db"), "failure_db": str(tmp_path / "unused-f.db")}
    cp = tmp_path / "job.yaml"
    cp.write_text(yaml.safe_dump(cfg))
    from ops_feedback.wiring.log_store import LogStore
    log = LogStore.postgres(log_url)
    log.send(envelope(pii_trace("s-1", "t-1")))
    log.send(envelope(make_trace("s-1", "t-2")))
    env = _scrub(OPS_DEID_SALT="test-salt-0123456789abcdef-0123456789abcdef", OPS_LOG_DATABASE_URL_FILE=_file(tmp_path, "lu", log_url),
                 OPS_FAILURE_DATABASE_URL_FILE=_file(tmp_path, "fu", fail_url))

    def go(*extra):
        r = subprocess.run([sys.executable, "-m", "ops_feedback.job", "--config", str(cp), *extra], env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        for s in _secrets_of(log_url):
            assert s not in r.stdout + r.stderr
        return json.loads(r.stdout)
    a = go()
    assert a["cases_published"] == 1 and a["no_failure"] == 1
    b = go("--reprocess")
    assert b["cases_published"] == 0 and b["cases_existing"] == 1
    assert not list(tmp_path.glob("*.db"))                       # SQLite 파일이 만들어지지 않았다


def test_ingest_api_on_postgres(tmp_path, request):
    log_url, fail_url = _pg_schema_url(request), _pg_schema_url(request)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = _scrub(OPS_INGEST_TOKEN="tok", OPS_READ_TOKENS="reader=rt", OPS_PORT=str(port),
                 OPS_LOG_DATABASE_URL_FILE=_file(tmp_path, "lu", log_url), OPS_FAILURE_DATABASE_URL_FILE=_file(tmp_path, "fu", fail_url))
    p = Proc([sys.executable, "-m", "ops_feedback.api"], env)
    try:
        base = f"http://127.0.0.1:{port}"
        p.wait_for("listening on")      # 기동은 로그 줄이라는 사건으로 안다(시계 없음)

        ready = json.loads(urllib.request.urlopen(base + "/readyz", timeout=HANG_GUARD_SECONDS).read())      # 매달림 감시
        assert ready["log_store"] == "postgresql" and ready["failure_store"] == "postgresql"
        body = json.dumps(envelope(make_trace("s-9", "t-9"))).encode()
        def post():
            rq = urllib.request.Request(base + "/v1/traces", data=body, headers={"Authorization": "Bearer tok", "Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(rq, timeout=HANG_GUARD_SECONDS).read())["status"]      # 매달림 감시
        assert [post(), post()] == ["ACCEPTED", "DUPLICATE"]
        text = json.dumps(ready)
        for sec in _secrets_of(log_url):
            assert sec not in text
    finally:
        out = p.stop()
    for sec in _secrets_of(log_url):
        assert sec not in out

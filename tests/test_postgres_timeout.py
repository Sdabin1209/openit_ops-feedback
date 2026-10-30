"""PostgreSQL이 TCP는 받되 응답이 없을 때(docker pause) 상한 안에 끝나는지. FAULT_MATRIX P0-3(D1·D4).

필요: TEST_DATABASE_URL(PostgreSQL)과 TEST_PG_CONTAINER(그 서버를 담은 컨테이너 이름, `docker pause`로 얼린다).
둘 중 하나라도 없거나 docker를 쓸 수 없으면 SKIP(PASS로 세지 않는다). 상한 값은 시험이 짧게 준다(OPS_DB_*_TIMEOUT).
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest
import yaml

from conftest import _pg_schema_url, base_config, envelope, make_trace
from test_postgres_select import _file, _scrub

CONTAINER = os.environ.get("TEST_PG_CONTAINER")
LIMITS = {"OPS_DB_CONNECT_TIMEOUT": "1", "OPS_DB_STATEMENT_TIMEOUT": "1", "OPS_DB_PROBE_TIMEOUT": "2"}


def _docker(*a):
    subprocess.run(["docker", *a], check=True, capture_output=True, timeout=30)


@pytest.fixture
def frozen_pg(request):
    if not CONTAINER or not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL/TEST_PG_CONTAINER 없음: PostgreSQL 무응답 시험 건너뜀")
    try:
        _docker("inspect", CONTAINER)
    except Exception:
        pytest.skip("docker로 컨테이너를 다룰 수 없다")
    class F:
        def pause(self): _docker("pause", CONTAINER)
        def resume(self):
            subprocess.run(["docker", "unpause", CONTAINER], capture_output=True, timeout=30)
    f = F()
    request.addfinalizer(f.resume)           # 시험이 실패해도 서버를 되살린다
    return f


def _serve(tmp_path, url_log, url_fail):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = _scrub(OPS_INGEST_TOKEN="tok", OPS_READ_TOKENS="reader=rt", OPS_REVIEW_TOKENS="reviewer=vt", OPS_REVIEW_REQUIRE_USER="0", OPS_PORT=str(port), OPS_READYZ_CACHE_SECONDS="0", **LIMITS,
                 OPS_LOG_DATABASE_URL_FILE=_file(tmp_path, "lu", url_log), OPS_FAILURE_DATABASE_URL_FILE=_file(tmp_path, "fu", url_fail))
    p = subprocess.Popen([sys.executable, "-m", "ops_feedback.api"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            urllib.request.urlopen(base + "/readyz", timeout=3).read(); break
        except Exception:
            time.sleep(0.5)
    else:
        p.kill()
        pytest.fail("readyz 응답 없음")
    return p, base


def _readyz(base):
    t0 = time.monotonic()
    try:
        r = urllib.request.urlopen(base + "/readyz", timeout=15)
        code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    return code, time.monotonic() - t0


def _post(base, tid):
    rq = urllib.request.Request(base + "/v1/traces", data=json.dumps(envelope(make_trace("s-9", tid))).encode(),
                                headers={"Authorization": "Bearer tok", "Content-Type": "application/json"})
    t0 = time.monotonic()
    try:
        code = urllib.request.urlopen(rq, timeout=30).status
    except urllib.error.HTTPError as e:
        code = e.code
    return code, time.monotonic() - t0


def _post_status(base, tid):
    rq = urllib.request.Request(base + "/v1/traces", data=json.dumps(envelope(make_trace("s-9", tid))).encode(),
                                headers={"Authorization": "Bearer tok", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(rq, timeout=15).read())["status"]


def _call(base, method, path, token, body=None):
    rq = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    t0 = time.monotonic()
    try:
        r = urllib.request.urlopen(rq, timeout=30)
        return r.status, json.loads(r.read()), time.monotonic() - t0
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), time.monotonic() - t0


def test_readyz_503_within_probe_limit_and_recovers(tmp_path, request, frozen_pg):
    p, base = _serve(tmp_path, _pg_schema_url(request), _pg_schema_url(request))
    try:
        assert _readyz(base)[0] == 200
        frozen_pg.pause()
        code, dt = _readyz(base)
        assert code == 503 and dt < 2.8, (code, dt)           # 상한 2초 + 프로세스·HTTP 여유
        frozen_pg.resume()
        for _ in range(20):
            code, dt = _readyz(base)
            if code == 200:
                break
            time.sleep(0.5)
        assert code == 200
    finally:
        frozen_pg.resume()
        p.terminate()
        p.communicate(timeout=10)


def test_readyz_not_blocked_by_main_connection_lock(tmp_path, request, frozen_pg):
    """요청 경로가 주 연결을 쥐고 기다리는 중에도 /readyz는 막히지 않는다."""
    p, base = _serve(tmp_path, _pg_schema_url(request), _pg_schema_url(request))
    try:
        frozen_pg.pause()
        res = {}
        t = threading.Thread(target=lambda: res.update(post=_post(base, "t-1")))
        t.start()
        time.sleep(0.3)                                       # 수집 요청이 주 연결에서 응답을 기다리는 중
        code, dt = _readyz(base)
        assert code == 503 and dt < 2.8, (code, dt)
        t.join(30)
    finally:
        frozen_pg.resume()
        p.terminate()
        p.communicate(timeout=10)


def test_request_path_is_bounded_and_returns_unavailable(tmp_path, request, frozen_pg):
    p, base = _serve(tmp_path, _pg_schema_url(request), _pg_schema_url(request))
    try:
        assert _post(base, "t-0")[0] in (200, 201)
        frozen_pg.pause()
        code, dt = _post(base, "t-1")
        # 시도 1: statement 1s + 1s 뒤 소켓 끊김, 재시도: 접속 1s. 합 약 3초 + 여유
        assert code == 503 and dt < 6, (code, dt)
        frozen_pg.resume()
        for _ in range(20):
            code, _ = _post(base, "t-1")
            if code in (200, 201):
                break
            time.sleep(0.5)
        assert code in (200, 201)                              # 복구 뒤 서비스 재시작 없이 수집
        # 같은 turn_id의 재전송: 첫 시도가 서버에 닿았는지 몰라도 기록은 한 건이다(ACCEPTED 뒤 DUPLICATE, 이중 기록 없음)
        assert _post_status(base, "t-1") == "DUPLICATE"
        assert _post_status(base, "t-new") == "ACCEPTED" and _post_status(base, "t-new") == "DUPLICATE"
    finally:
        frozen_pg.resume()
        p.terminate()
        p.communicate(timeout=10)


def test_job_exits_nonzero_when_database_frozen(tmp_path, request, frozen_pg):
    cfg = base_config()
    cfg["storage"] = {"log_db": str(tmp_path / "unused-l.db"), "failure_db": str(tmp_path / "unused-f.db")}
    cf = tmp_path / "job.yaml"
    cf.write_text(yaml.safe_dump(cfg))
    env = _scrub(OPS_DEID_SALT="test-salt-0123456789abcdef-0123456789abcdef", **LIMITS,
                 OPS_LOG_DATABASE_URL_FILE=_file(tmp_path, "lu", _pg_schema_url(request)),
                 OPS_FAILURE_DATABASE_URL_FILE=_file(tmp_path, "fu", _pg_schema_url(request)))
    ok = subprocess.run([sys.executable, "-m", "ops_feedback.job", "--config", str(cf), "--no-rollback"], env=env,
                        capture_output=True, text=True, timeout=60)
    assert ok.returncode in (0, 1), ok.stderr
    frozen_pg.pause()
    t0 = time.monotonic()
    try:
        r = subprocess.run([sys.executable, "-m", "ops_feedback.job", "--config", str(cf), "--no-rollback"], env=env,
                           capture_output=True, text=True, timeout=60)  # 무한 대기하면 timeout으로 시험이 실패한다
    finally:
        frozen_pg.resume()            # 스키마 정리(finalizer)가 서버 응답을 필요로 하므로 단언 전에 되살린다
    dt = time.monotonic() - t0
    assert r.returncode == 2 and dt < 15, (r.returncode, dt, r.stderr)
    assert "Traceback" not in r.stderr and "UNAVAILABLE" in r.stderr


def test_invalid_timeout_setting_is_rejected(monkeypatch):
    from boundary_contracts.errors import BoundaryError
    from ops_feedback.wiring import pgbase
    monkeypatch.setenv("OPS_DB_STATEMENT_TIMEOUT", "0")
    with pytest.raises(BoundaryError):
        pgbase._setting("OPS_DB_STATEMENT_TIMEOUT")
    monkeypatch.setenv("OPS_DB_STATEMENT_TIMEOUT", "abc")
    with pytest.raises(BoundaryError):
        pgbase._setting("OPS_DB_STATEMENT_TIMEOUT")
    monkeypatch.delenv("OPS_DB_STATEMENT_TIMEOUT")
    assert pgbase._setting("OPS_DB_STATEMENT_TIMEOUT") == 3.0


def test_read_and_review_are_bounded_when_database_frozen_and_review_is_retry_safe(tmp_path, request, frozen_pg):
    """D5(I7 읽기)와 D6(검토 게시)의 DB 무응답: 상한 안에 503, 반쯤 쓴 상태 없이 같은 요청을 다시 보내면 한 번만 반영된다."""
    from ops_feedback.components.failure_case_store import FailureCaseStore as FailureStoreImpl
    from test_conformance_failurestore import TRACE, case
    log_url, fail_url = _pg_schema_url(request), _pg_schema_url(request)
    seed = FailureStoreImpl.postgres(fail_url)
    seed.put_trace("tr-1", TRACE)
    seed.publish_case(case())
    seed._b.close()
    p, base = _serve(tmp_path, log_url, fail_url)
    try:
        assert _call(base, "GET", "/v1/cases", "rt")[0] == 200
        frozen_pg.pause()
        code, body, dt = _call(base, "GET", "/v1/cases", "rt")
        assert code == 503 and dt < 9, (code, dt)
        code, body, dt = _call(base, "POST", "/v1/cases/fc-1/review", "vt", {"review_status": "DISMISSED"})
        assert code == 503 and dt < 9, (code, dt)
        frozen_pg.resume()
        for _ in range(20):
            code, body, _ = _call(base, "POST", "/v1/cases/fc-1/review", "vt", {"review_status": "DISMISSED"})
            if code in (200, 201):
                break
            time.sleep(0.5)
        assert code in (200, 201) and body["version"] == 2
        assert _call(base, "POST", "/v1/cases/fc-1/review", "vt", {"review_status": "DISMISSED"})[0] == 200      # 재전송: 새 version 없음
        items = _call(base, "GET", "/v1/cases?latest_only=false", "rt")[1]["items"]
        assert [c["version"] for c in items] == [1, 2]
    finally:
        frozen_pg.resume()
        p.terminate()
        p.communicate(timeout=10)

"""RUNTIME 조항 시험: R8.1(503 로그에 예외 종류·위치), R10.3(계약 클라이언트 timeout 기본을 덮어쓰지 않음), R1.8(release-gate 클라이언트에 출구), R10.5(로그 쓰기 실패가 요청을 실패시키지 않음),
R3.10·R4.1(PostgreSQL: 쓰기 불가면 /readyz 503·쓰기 503, 연결이 끊겨도 재시작 없이 복구)."""
from __future__ import annotations

import logging
import os

import pytest
from boundary_contracts.errors import BoundaryError

import ops_feedback.components.link_ingest_api as api
from waiting import HANG_GUARD_SECONDS
from conftest import _pg_schema_url, envelope, make_trace
from ops_feedback.wiring.log_store import LogStore


CAPTURED: dict = {}


class CaptureClient:
    """HttpReleaseReader 대역: build 가 클라이언트 생성에 넘기는 인자를 그대로 기록한다(설정이 아니라 클라이언트 모듈의 이름을 바꿔 끼운다, D135)."""

    def __init__(self, *a, **kw):
        CAPTURED.clear()
        CAPTURED.update(kw, base_url=a[0], token=a[1])


def _build(tmp_path, settings, env_extra, monkeypatch):
    import yaml
    from boundary_contracts import clients
    from conftest import base_config
    from ops_feedback.config import load_config
    from ops_feedback.wiring.build import build_from_config
    monkeypatch.setattr(clients, "HttpReleaseReader", CaptureClient)
    cfg = base_config()
    cfg["storage"] = {"log_db": str(tmp_path / "l.db"), "failure_db": str(tmp_path / "f.db")}
    cfg["release_reader"] = {"kind": "http", "token_secret": "OPS_GATE_READ_TOKEN", **settings}
    p = tmp_path / "job.yaml"
    p.write_text(yaml.safe_dump(cfg))
    build_from_config(load_config(p), {"OPS_DEID_SALT": "test-salt-0123456789abcdef-0123456789abcdef", "OPS_GATE_READ_TOKEN": "t", **env_extra})
    return dict(CAPTURED)


def test_r10_3_build_does_not_override_the_client_timeout(tmp_path, monkeypatch):
    """R10.3: 호출자 설정에 값이 없으면 계약 클라이언트 기본(10초)이다: 우리 배선은 timeout 을 만들어 넣지 않는다. 설정(timeout_seconds)이 있으면 그 값이 간다."""
    assert "timeout" not in _build(tmp_path, {"base_url": "https://release-gate"}, {}, monkeypatch)
    assert _build(tmp_path, {"base_url": "https://release-gate", "timeout_seconds": 5}, {}, monkeypatch)["timeout"] == 5


def test_r1_8_release_gate_client_gets_the_egress_proxy_from_the_environment(tmp_path, monkeypatch):
    """R1.8·CONVENTIONS 13절: 영역 밖(release-gate) 클라이언트 생성에 OPENIT_EGRESS_PROXY 하나만 꺼내 egress_proxy 로 넘긴다. 없으면 None."""
    assert _build(tmp_path, {"base_url": "https://release-gate"}, {"OPENIT_EGRESS_PROXY": "http://127.0.0.1:3128"}, monkeypatch)["egress_proxy"] == "http://127.0.0.1:3128"
    assert _build(tmp_path, {"base_url": "https://release-gate"}, {}, monkeypatch)["egress_proxy"] is None


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


def test_r8_1_unexpected_handler_exception_logs_kind_not_message(caplog):
    """R8.1: 핸들러의 예상 밖 예외도 503 이고, 로그에는 예외 클래스 이름과 위치만 남고 메시지(환자 발화일 수 있다)는 남지 않는다."""
    import json, threading
    from test_http_401 import ING, call
    from ops_feedback.wiring.ingest_service import make_ingest_server
    log = LogStore.memory()

    def boom(_envelope):
        raise RuntimeError("SECRET-PATIENT-TEXT")
    log.send = boom
    s = make_ingest_server(log, ING, "127.0.0.1", 0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        with caplog.at_level(logging.ERROR, logger="ingest-api"):
            st, _, _ = call(s.server_address[1], "POST", "/v1/traces", f"Bearer {ING}", body=json.dumps(envelope(make_trace("s-x", "t-1"))).encode())
    finally:
        s.shutdown()
    assert st == 503 and "RuntimeError" in caplog.text and "SECRET-PATIENT-TEXT" not in caplog.text


def test_r10_5_log_write_failure_does_not_fail_the_request():
    """R10.5: 표준 출력(로그 핸들러)이 막혀 쓰기에 실패해도 요청의 출력은 바뀌지 않는다(수집 200 ACCEPTED)."""
    import json, threading
    from test_http_401 import ING, call
    from ops_feedback.wiring.ingest_service import make_ingest_server

    class Full:
        def write(self, *a):
            raise OSError("disk full")

        def flush(self):
            raise OSError("disk full")
    lg = logging.getLogger("ingest-api")
    h = logging.StreamHandler(Full())
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    old = logging.raiseExceptions
    logging.raiseExceptions = False
    s = make_ingest_server(LogStore.memory(), ING, "127.0.0.1", 0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        st, _, body = call(s.server_address[1], "POST", "/v1/traces", f"Bearer {ING}", body=json.dumps(envelope(make_trace("s-l", "t-1"))).encode())
        assert st == 200 and body["status"] == "ACCEPTED"
    finally:
        s.shutdown()
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
    store.ping()                                       # 쓸 수 있을 때는 준비됨이다(항상 거부하는 구현을 거른다)
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
    store.ping()                                                                         # 복구되면 준비됨으로 돌아온다
    assert store.send(envelope(make_trace("s-ro", "t-2")))["status"] == "ACCEPTED"      # 복구되면 재시작 없이 다시 쓴다


def test_c18_f19_resent_turn_with_other_content_is_200_duplicate_over_http():
    """CELLS C18 F19: 같은 (session_id, turn_id)를 다른 내용으로 다시 보내면 200 IngestResult DUPLICATE(409 아님), 먼저 받은 것이 남는다."""
    import json, threading
    from test_http_401 import ING, call
    from ops_feedback.wiring.ingest_service import make_ingest_server
    s = make_ingest_server(LogStore.memory(), ING, "127.0.0.1", 0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        a = json.dumps(envelope(make_trace("s-d", "t-1"))).encode()
        b = json.dumps(envelope(make_trace("s-d", "t-1"), release_id="rel-0003")).encode()
        st1, _, r1 = call(s.server_address[1], "POST", "/v1/traces", f"Bearer {ING}", body=a)
        st2, _, r2 = call(s.server_address[1], "POST", "/v1/traces", f"Bearer {ING}", body=b)
        assert (st1, r1["status"]) == (200, "ACCEPTED")
        assert st2 == 200 and r2["status"] == "DUPLICATE" and r2["session_id"] == "s-d" and r2["turn_id"] == "t-1"
    finally:
        s.shutdown()


def test_r5_1_readyz_503_body_is_not_ready_and_passes_both_schemas(monkeypatch):
    """R5.1: 저장소가 안 될 때 /readyz 는 503 + status not_ready, 본문이 HealthStatus 와 OpsFeedbackReadyz 를 모두 통과한다. /healthz·/readyz·/metrics 는 인증 없음."""
    import json, threading
    from boundary_contracts import validator
    from test_http_401 import ING, call
    from ops_feedback.wiring.ingest_service import make_ingest_server
    store = LogStore.memory()
    monkeypatch.setattr(store, "ping", lambda: (_ for _ in ()).throw(BoundaryError("UNAVAILABLE", "x")))
    s = make_ingest_server(store, ING, "127.0.0.1", 0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        p = s.server_address[1]
        st, _, body = call(p, "GET", "/readyz")
        assert st == 503 and body["status"] == "not_ready" and body["code"] == "UNAVAILABLE"
        for name in ("HealthStatus", "OpsFeedbackReadyz"):
            assert not list(validator(name).iter_errors(body)), name
        assert call(p, "GET", "/healthz")[0] == 200
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", p, timeout=HANG_GUARD_SECONDS)
        c.request("GET", "/metrics", headers={"Authorization": "Bearer wrong"})        # 토큰이 틀려도 무시한다(401·403 없음)
        r = c.getresponse()
        text = r.read().decode()
        assert r.status == 200 and r.getheader("Content-Type").startswith("text/plain") and "# TYPE ops_feedback_ingest_accepted_total counter" in text     # R10.11: 저장소가 멈춘 동안에도 200, 인증 없음
    finally:
        s.shutdown()

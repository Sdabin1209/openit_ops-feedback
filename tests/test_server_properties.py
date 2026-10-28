"""HTTP 서버가 지켜야 할 성질(ADR 0020): 느린 클라이언트가 다른 요청을 막지 않는다, 멈춘 연결은 timeout으로 닫힌다,
종료할 때 진행 중 요청을 마친다, 연결 상한을 넘는 연결은 스레드를 늘리지 않는다, 큰 본문은 빨리 거부한다. 서버 구현(표준 라이브러리 → waitress)을 바꿔도 같아야 한다."""
import http.client
import json
import socket
import threading
import time

import pytest

import ops_feedback.api as api
from conftest import envelope, make_trace
from ops_feedback.logstore import LogStore


def start(**kw):
    s = api.make_server(kw.pop("store", None) or LogStore.memory(), "tok", "127.0.0.1", 0, **kw)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, s.server_address[1]


def stuck(port, n):
    out = []
    for _ in range(n):
        c = socket.create_connection(("127.0.0.1", port))
        c.sendall(b"POST /v1/traces HTTP/1.1\r\nAuthorization: Bearer tok\r\nContent-Length: 100\r\n\r\n")     # 본문을 덜 보내고 붙들고 있는 연결
        out.append(c)
    return out


def test_stuck_clients_do_not_block_other_requests():
    s, port = start()
    held = stuck(port, 10)
    try:
        time.sleep(0.3)
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        t = time.perf_counter()
        c.request("GET", "/healthz")
        assert c.getresponse().status == 200 and time.perf_counter() - t < 2
    finally:
        for h in held:
            h.close()
        s.shutdown()


def test_stuck_connection_is_closed_by_the_timeout(monkeypatch):
    monkeypatch.setattr(api, "REQUEST_TIMEOUT", 1.0)
    s, port = start()
    (h,) = stuck(port, 1)
    try:
        h.settimeout(6)
        t = time.perf_counter()
        try:
            data = h.recv(4096)          # 서버가 닫으면 b"" (또는 오류 응답 뒤 닫힘)
        except ConnectionResetError:
            data = b""
        assert time.perf_counter() - t < 5
        assert data == b"" or data.startswith(b"HTTP/1.")
    finally:
        h.close()
        s.shutdown()


def test_shutdown_finishes_the_request_in_flight():
    store = LogStore.memory()
    real = store.send

    def slow(env):
        time.sleep(1.0)
        return real(env)
    store.send = slow
    s, port = start(store=store)
    box = {}

    def post():
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("POST", "/v1/traces", body=json.dumps(envelope(make_trace("s-1", "t-1"))), headers={"Authorization": "Bearer tok"})
        box["status"] = c.getresponse().status
    th = threading.Thread(target=post)
    th.start()
    time.sleep(0.3)
    s.shutdown()                                                   # 종료 신호와 같은 경로
    th.join(10)
    assert box.get("status") == 200
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=1.5)
    with pytest.raises(OSError):                                   # 종료 뒤 새 요청은 받지 않는다
        c.request("GET", "/healthz")
        c.getresponse()


def test_connections_over_the_cap_wait_instead_of_spawning_workers():
    s, port = start(max_connections=2)
    held = stuck(port, 2)
    try:
        time.sleep(0.3)
        c = socket.create_connection(("127.0.0.1", port), timeout=1.5)
        c.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n")
        with pytest.raises(socket.timeout):                         # 상한이 찬 동안은 응답이 없다(대기열). 한 칸이 비면 처리된다
            c.recv(100)
        held[0].close()
        c.settimeout(5)
        assert c.recv(100).startswith(b"HTTP/1.1 200")
        c.close()
    finally:
        for h in held:
            h.close()
        s.shutdown()


def test_oversized_ingest_body_is_rejected_fast():
    s, port = start()
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        t = time.perf_counter()
        try:
            c.request("POST", "/v1/traces", body=b"a" * (9 * 1024 * 1024), headers={"Authorization": "Bearer tok"})
            st = c.getresponse().status
        except (BrokenPipeError, ConnectionResetError):
            st = 413
        assert st in (400, 413) and time.perf_counter() - t < 5
    finally:
        s.shutdown()

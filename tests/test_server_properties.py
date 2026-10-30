"""HTTP 서버가 지켜야 할 성질(ADR 0020): 느린 클라이언트가 다른 요청을 막지 않는다, 멈춘 연결은 timeout으로 닫힌다,
종료할 때 진행 중 요청을 마친다, 연결 상한을 넘는 연결은 스레드를 늘리지 않는다, 큰 본문은 빨리 거부한다. 서버 구현(표준 라이브러리 → waitress)을 바꿔도 같아야 한다."""
import http.client
import json
import socket
import threading
import time

import pytest

import ops_feedback.components.link_ingest_api as api
from ops_feedback.wiring.ingest_service import make_ingest_server
from conftest import envelope, make_trace
from ops_feedback.wiring.log_store import LogStore


def start(**kw):
    s = make_ingest_server(kw.pop("store", None) or LogStore.memory(), "tok", "127.0.0.1", 0, **kw)
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
        # 판정은 "붙들린 연결 10개가 있어도 /healthz가 응답한다"이다. 속도가 아니라서 벽시계 상한을 두지 않는다(바깥 마감은 클라이언트 timeout 30초: 막히면 여기서 실패한다)
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        c.request("GET", "/healthz")
        assert c.getresponse().status == 200
    finally:
        for h in held:
            h.close()
        s.shutdown()


def test_stuck_connection_is_closed_by_the_timeout(monkeypatch):
    monkeypatch.setattr(api, "REQUEST_TIMEOUT", 1.0)
    s, port = start()
    (h,) = stuck(port, 1)
    try:
        # 서버 timeout은 1초다. 시험이 확인하는 것은 "닫힌다(매달리지 않는다)"이므로 바깥 마감을 30초로 넉넉히 둔다(부하에서 수 초 늦어도 판정이 같다).
        # 30초 안에 닫히지 않으면 recv가 socket.timeout으로 실패한다.
        h.settimeout(30)
        try:
            data = h.recv(4096)          # 서버가 닫으면 b"" (또는 오류 응답 뒤 닫힘)
        except ConnectionResetError:
            data = b""
        assert data == b"" or data.startswith(b"HTTP/1.")
    finally:
        h.close()
        s.shutdown()


def test_shutdown_finishes_the_request_in_flight():
    store = LogStore.memory()
    real = store.send
    entered, release = threading.Event(), threading.Event()

    def slow(env):
        entered.set()                    # 요청이 서버 안에서 처리 중임을 알린다(시계 대신 사건으로 순서를 잡는다)
        assert release.wait(30)
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
    assert entered.wait(30)
    threading.Timer(0.5, release.set).start()                      # 종료가 시작된 뒤에 처리가 끝나도록 놓아 준다(종료가 진행 중 요청을 기다리는지 본다)
    s.shutdown()                                                   # 종료 신호와 같은 경로
    th.join(30)
    assert box.get("status") == 200
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=1.5)
    with pytest.raises(OSError):                                   # 종료 뒤 새 요청은 받지 않는다
        c.request("GET", "/healthz")
        c.getresponse()


def test_connections_over_the_cap_wait_instead_of_spawning_workers():
    s, port = start(max_connections=2)
    held = stuck(port, 2)      # 먼저 연결한 순서대로 받아들여지므로 c보다 앞선다(같은 호스트의 커널 대기열 순서)
    try:
        c = socket.create_connection(("127.0.0.1", port), timeout=1.5)
        c.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n")
        with pytest.raises(socket.timeout):                         # 상한이 찬 동안은 응답이 없다(대기열). 한 칸이 비면 처리된다
            c.recv(100)
        held[0].close()
        c.settimeout(30)      # 한 칸이 비면 처리된다: 바깥 마감만 넉넉히(부하 여유)
        assert c.recv(100).startswith(b"HTTP/1.1 200")
        c.close()
    finally:
        for h in held:
            h.close()
        s.shutdown()


def test_oversized_ingest_body_is_rejected_fast():
    """큰 Content-Length 머리글만 보내고 응답을 읽는다. 서버는 본문을 기다리지 않고 413으로 거절해야 한다(본문을 보내지 않으므로 전송 중 연결 끊김 경쟁이 없다)."""
    s, port = start()
    try:
        c = socket.create_connection(("127.0.0.1", port), timeout=30)
        c.sendall(b"POST /v1/traces HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer tok\r\nContent-Length: %d\r\n\r\n" % (9 * 1024 * 1024))
        line = b""
        while b"\r\n" not in line:
            chunk = c.recv(4096)
            if not chunk:
                break
            line += chunk
        assert line.split()[1] in (b"400", b"413"), line[:80]
        c.close()
    finally:
        s.shutdown()

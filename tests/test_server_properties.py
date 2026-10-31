"""HTTP 서버 면(ADR 0020, 0026). 느린 연결이 언제 끊기는가, 연결 상한을 넘으면 어떻게 되는가는 waitress의 동작이라 시험하지 않는다(의존 자체는 시험하지 않는다).
이 영역의 책임은 둘이다: (1) waitress와 openit_link에 올바른 값을 넘겼는가 (2) 종료 절차(Server.shutdown)가 정해진 순서로 호출하는가. 시간을 재지 않는다(U421)."""
import io
import threading

import pytest

import ops_feedback.components.link_ingest_api as api
from ops_feedback.components.failure_case_store import FailureCaseStore
from ops_feedback.wiring.ingest_service import IngestService, make_ingest_server
from ops_feedback.wiring.log_store import LogStore
from fakeclock import FakeClock
from waiting import HANG_GUARD_SECONDS


class FakeWaitress:
    """waitress.create_server 대역: 넘긴 인자를 기록한다."""

    def __init__(self):
        self.kw = {}
        self.calls = []

    def create_server(self, app, **kw):
        self.kw = kw
        outer = self

        class Dispatcher:
            def shutdown(self, **k):
                outer.calls.append(("dispatcher.shutdown", k))

        class Srv:
            effective_host, effective_port = "127.0.0.1", 4321
            task_dispatcher = Dispatcher()
            _map = {}

            def close(self):
                outer.calls.append(("close", {}))

            def run(self):
                outer.calls.append(("run", {}))
                raise OSError("EBADF")
        return Srv()


@pytest.fixture
def fake(monkeypatch):
    f = FakeWaitress()
    monkeypatch.setattr(api.waitress, "create_server", f.create_server)
    monkeypatch.setattr(api.waitress.wasyncore, "close_all", lambda m: f.calls.append(("close_all", {})))
    return f


def test_waitress_receives_our_limits(fake, monkeypatch):
    """연결 상한 N이면 일꾼 N개, waitress 상한 N+2(듣는 소켓·깨우기 포함), 멈춘 연결 정리 주기, 본문 상한을 넘긴다(ADR 0020)."""
    api.Server(lambda *a: None, "127.0.0.1", 0, connection_limit=7, channel_timeout=12.5, clock=FakeClock(), sleep=FakeClock().sleep)      # 넘긴 값 확인(시계 아님)
    kw = fake.kw
    assert kw["threads"] == 7 and kw["connection_limit"] == 9
    assert kw["channel_timeout"] == 12.5 and kw["cleanup_interval"] == 5.0           # min(5, channel_timeout)
    assert kw["max_request_body_size"] == api.MAX_BODY
    api.Server(lambda *a: None, "127.0.0.1", 0, connection_limit=1, channel_timeout=2.0, clock=FakeClock(), sleep=FakeClock().sleep)      # 넘긴 값 확인(시계 아님)
    assert fake.kw["cleanup_interval"] == 2.0


def test_make_server_passes_the_request_timeout_and_connection_cap(fake, monkeypatch):
    monkeypatch.setattr(api, "REQUEST_TIMEOUT", 3.5)
    make_ingest_server(LogStore.memory(), "tok", "127.0.0.1", 0, max_connections=5)
    assert fake.kw["channel_timeout"] == 3.5 and fake.kw["threads"] == 5 and fake.kw["connection_limit"] == 7


def test_shutdown_stops_listening_then_lets_in_flight_requests_finish_then_closes(fake):
    """종료 절차: 듣는 소켓을 닫고 -> 진행 중 요청을 취소하지 않고 grace까지 기다리고 -> 연결을 닫는다."""
    s = api.Server(lambda *a: None, "127.0.0.1", 0, connection_limit=2, channel_timeout=1.0, clock=FakeClock(), sleep=FakeClock().sleep)      # 넘긴 값 확인(시계 아님)
    s.shutdown(grace=123.0)
    assert [c[0] for c in fake.calls] == ["close", "dispatcher.shutdown", "close_all"]
    assert fake.calls[1][1] == {"cancel_pending": False, "timeout": 123.0}
    assert api.SHUTDOWN_GRACE_SECONDS < 10      # docker stop 기본 10초(SIGKILL 전)보다 짧다(ADR 0020의 제약값)


def test_serve_forever_hides_the_socket_error_only_while_closing(fake):
    s = api.Server(lambda *a: None, "127.0.0.1", 0, connection_limit=2, channel_timeout=1.0, clock=FakeClock(), sleep=FakeClock().sleep)      # 넘긴 값 확인(시계 아님)
    with pytest.raises(OSError):
        s.serve_forever()                  # 종료 중이 아니면 오류를 올린다
    s._closing = True
    s.serve_forever()                      # 종료 중이면 정상 종료로 본다


def wsgi(app, method, path, length, token):
    got = {}
    env = {"REQUEST_METHOD": method, "PATH_INFO": path, "CONTENT_LENGTH": str(length), "HTTP_AUTHORIZATION": f"Bearer {token}",
           "wsgi.input": io.BytesIO(b""), "CONTENT_TYPE": "application/json"}
    app(env, lambda status, headers, *a: got.setdefault("status", status))
    return got["status"]


def test_ingest_surface_refuses_a_body_over_its_limit_without_reading_it():
    """수집 면 본문 상한(8 MiB)이 openit_link Route에 실제로 넘어갔는지: 상한보다 큰 Content-Length 머리글만 있는 요청이 413이다(본문·소켓·시간 없음)."""
    app = api.make_app(IngestService(LogStore.memory()), "tok", clock=FakeClock())
    assert wsgi(app, "POST", "/v1/traces", api.MAX_BODY + 1, "tok").startswith("413")


def test_one_real_round_trip_starts_answers_and_stops():
    """기동 -> /healthz 한 번 -> 종료. 서버를 끝까지 맞물려 보는 일은 deploy E2E·smoke의 몫이다."""
    import http.client
    s = make_ingest_server(LogStore.memory(), "tok", "127.0.0.1", 0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=HANG_GUARD_SECONDS)      # 매달림 감시
        c.request("GET", "/healthz")
        assert c.getresponse().status == 200
    finally:
        s.shutdown()


# ---- 시계·대기 주입(ADR 0027) ----

class StubService:
    def __init__(self):
        self.pinged = 0

    def pings(self):
        return [self._ping]

    def _ping(self):
        self.pinged += 1

    def supported_versions(self):
        return [1]

    def log_kind(self):
        return "memory"

    def failure_kind(self):
        return None


def test_readyz_result_is_shared_for_the_cache_window_by_the_injected_clock():
    """readyz 캐시: 같은 창 안의 호출은 저장소를 다시 확인하지 않고, 창이 지나면(가짜 시계를 흘림) 다시 확인한다. 실제 시간을 기다리지 않는다."""
    now = [1000.0]
    svc = StubService()
    app = api.make_app(svc, "tok", readyz_cache_seconds=1.5, clock=lambda: now[0])

    def readyz():
        got = {}
        app({"REQUEST_METHOD": "GET", "PATH_INFO": "/readyz", "wsgi.input": io.BytesIO(b"")}, lambda st, h, *a: got.setdefault("st", st))
        return got["st"]
    assert readyz().startswith("200") and svc.pinged == 1
    now[0] += 1.4
    assert readyz().startswith("200") and svc.pinged == 1          # 창 안: 다시 확인하지 않는다
    now[0] += 0.2
    assert readyz().startswith("200") and svc.pinged == 2          # 창 밖(1.6초 지남): 다시 확인한다


def test_shutdown_flush_loop_ends_when_the_injected_clock_passes_its_limit(fake):
    """종료 때 응답이 소켓으로 나가길 기다리는 루프는 SHUTDOWN_FLUSH_SECONDS 뒤에 끝난다(가짜 시계·대기로 흘림, 실제 대기 없음)."""
    now, slept = [0.0], []

    class Busy:
        total_outbufs_len = 5      # 보낼 것이 남아 있는 연결

    s = api.Server(lambda *a: None, "127.0.0.1", 0, connection_limit=2, channel_timeout=1.0,
                   clock=lambda: now[0], sleep=lambda secs: (slept.append(secs), now.__setitem__(0, now[0] + secs)))      # 넘긴 값 확인(시계 아님)
    s._srv._map = {1: Busy()}
    s.shutdown(grace=1.0)
    assert slept and abs(sum(slept) - api.SHUTDOWN_FLUSH_SECONDS) < 0.05 and fake.calls[-1][0] == "close_all"


# ---- R7: 목록 응답을 내보내기 전에 계약으로 검증한다(U424) ----

class ListStub(StubService):
    read_enabled = True
    review_enabled = False

    def __init__(self, result):
        super().__init__()
        self.result = result

    def list_cases(self, **kw):
        return self.result


def list_call(result):
    app = api.make_app(ListStub(result), "tok", read_tokens={"rt": "reader"}, clock=FakeClock())
    got, body = {}, []

    def sr(status, headers, *a):
        got["status"] = status
    body = app({"REQUEST_METHOD": "GET", "PATH_INFO": "/v1/cases", "QUERY_STRING": "", "wsgi.input": io.BytesIO(b""), "HTTP_AUTHORIZATION": "Bearer rt"}, sr)
    return got["status"], b"".join(body)


def test_case_list_envelope_is_valid_for_the_contract_before_it_is_sent():
    import json
    case = json.loads((api_example()).read_text("utf-8"))
    st, body = list_call(([case], None))
    assert st.startswith("200") and json.loads(body)["next_cursor"] is None
    st, body = list_call(([case], "c1"))
    assert st.startswith("200") and json.loads(body)["next_cursor"] == "c1"


def test_a_broken_envelope_is_not_sent():
    """봉투가 계약 ListEnvelope를 어기면(next_cursor가 빈 문자열) 응답하지 않고 서버 결함(503)으로 닫는다."""
    st, body = list_call(([], ""))
    assert st.startswith("503") and b"UNAVAILABLE" in body


def test_a_broken_element_is_not_sent():
    st, body = list_call(([{"case_id": "x"}], None))
    assert st.startswith("503") and b"UNAVAILABLE" in body and b'"case_id"' not in body      # 원소가 FailureCase를 어기면 서버 결함: 내보내지 않고 503


def api_example():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent / "examples" / "output.FailureCase.json"

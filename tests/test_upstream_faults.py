"""이웃·중복·중단 장애 시험 (FAULT_MATRIX D2·D3·D5·D6·A6 수집 서버 쪽, FAULT_POLICY 원칙 2·4·9·10·12).

가짜 상류는 실제 HTTP 서버다. boundary_contracts의 실제 클라이언트(HttpReleaseReader, HttpGateService)가 멈춤·느림·5xx·깨진 JSON·401/403·
형식이 다른 응답을 어떻게 겪는지를 잡 수준(run_once)에서 본다. PostgreSQL이 필요한 것은 pipeline/log_store 매개변수에 따라 TEST_DATABASE_URL이 없으면 SKIP.
"""
import copy
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from boundary_contracts import PIPELINE_SCHEMA_VERSION
from boundary_contracts.clients import HttpGateService, HttpReleaseReader
from boundary_contracts.errors import BoundaryError

from conftest import envelope, make_trace, pii_trace
from fakes import FakeReleaseReader
from ops_feedback.failurestore import FailureStoreImpl
from ops_feedback.job import run_once
from ops_feedback.rollback import RollbackPlanner
from test_conformance_failurestore import case as mk_case, TRACE

NOW = "2026-10-15T00:00:00Z"
POLICY = {"window_hours": 24, "min_turns": 4, "max_failure_rate": 0.2, "counted_review_statuses": ["PENDING", "CONFIRMED"],
          "requested_by_role": "ops-feedback", "auto_submit": True}
POINTER = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "release_id": "rel-0002", "pointer_version": 1,
           "activated_at": "2026-10-15T00:00:00Z", "cause": "ACTIVATE"}


def receipt(req):
    return {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "request_id": req["request_id"], "status": "APPLIED", "decided_at": "2026-10-15T01:00:00Z",
            "active_pointer": dict(POINTER, release_id=req["to_release_id"], pointer_version=2, cause="ROLLBACK")}


class Upstream:
    """경로 접두사별로 동작을 바꿀 수 있는 가짜 게이트. mode: ok | hang | slow | 500 | 503 | badjson | 401 | 403 | shape | wrongid"""

    def __init__(self):
        self.mode = {}
        self.posts = []
        up = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, raw=False):
                data = body if raw else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self, body):
                mode = next((m for p, m in up.mode.items() if self.path.startswith(p)), "ok")
                if mode == "hang":
                    time.sleep(4)               # 클라이언트 timeout(1초)보다 길게 무응답
                    return
                if mode == "slow":
                    time.sleep(0.4)
                if mode in ("500", "503"):
                    return self._send(int(mode), {"code": "UNAVAILABLE", "message": "x"})
                if mode == "badjson":
                    return self._send(200, b"{not json", raw=True)
                if mode in ("401", "403"):
                    return self._send(int(mode), {"code": "PERMISSION_DENIED", "message": "토큰"})
                if mode == "shape":
                    return self._send(200, {"unexpected": True})
                if self.path.startswith("/v1/rollback"):
                    r = receipt(body)
                    if mode == "wrongid":
                        r["request_id"] = "rb-someone-else"
                    return self._send(200, r)
                if self.path.startswith("/v1/active-pointer"):
                    return self._send(200, {"item": POINTER})
                self._send(404, {"code": "NOT_FOUND", "message": "x"})

            def do_GET(self):
                self._handle(None)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                up.posts.append(body)
                self._handle(body)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"


@pytest.fixture
def upstream():
    u = Upstream()
    yield u
    u.srv.shutdown()


def run(p, planner, cfg=None, **kw):
    return run_once(cfg=cfg or p["cfg"], log=p["log"], store=p["store"], deid=p["deid"], detectors=p["detectors"],
                    classifier=p["classifier"], clock=lambda: "2026-10-14T12:00:00Z", planner=planner, **kw)


def seed(p, fails=3, oks=3):
    for i in range(fails):
        p["log"].send(envelope(pii_trace("s", f"f{i}"), release_id="rel-0002", sent_at=NOW))
    for i in range(oks):
        p["log"].send(envelope(make_trace("s", f"o{i}"), release_id="rel-0002", sent_at=NOW))


def planner(p, upstream, reader=None):
    gate = HttpGateService(upstream.url, "t", timeout=1)
    rr = reader or FakeReleaseReader(["rel-0002", "rel-0001"], "rel-0002")
    return RollbackPlanner(store=p["store"], log=p["log"], release_reader=rr, gate=gate, policy=POLICY, clock=lambda: NOW)


# ---------- D3: I5 롤백 요청 ----------

@pytest.mark.parametrize("mode,code", [("hang", "UNAVAILABLE"), ("500", "UNAVAILABLE"), ("503", "UNAVAILABLE"), ("badjson", "UNAVAILABLE"),
                                       ("401", "PERMISSION_DENIED"), ("403", "PERMISSION_DENIED"), ("shape", "INTEGRITY_ERROR"),
                                       ("wrongid", "INTEGRITY_ERROR")])
def test_gate_fault_is_bounded_visible_and_retried_with_same_request(pipeline, upstream, mode, code):
    seed(pipeline)
    pl = planner(pipeline, upstream)
    upstream.mode["/v1/rollback"] = mode
    t0 = time.monotonic()
    rep = run(pipeline, pl)
    assert time.monotonic() - t0 < 3.5                                   # 멈춤도 클라이언트 timeout(1초) 안에 끝난다
    assert rep.rollback["action"] == "REQUEST_CREATED" and rep.rollback["submit_error"] == code
    assert {"stage": "rollback_submit", "error": code} in rep.errors     # 조용히 버리지 않는다: 종료 코드 1의 근거
    rid = rep.rollback["request_id"]
    assert pipeline["store"].get_aux("rollback_request", rid) is not None
    assert pipeline["store"].get_aux("rollback_receipt", rid) is None    # 믿을 수 없는 응답은 영수증으로 저장하지 않는다
    upstream.mode.clear()                                                # 복구
    rep2 = run(pipeline, pl)
    assert rep2.rollback["request_id"] == rid and rep2.rollback["receipt_status"] == "APPLIED" and not rep2.errors
    assert {r["request_id"] for r in upstream.posts} == {rid}            # 재전송은 같은 request_id(게이트가 멱등으로 받는다)
    assert len(pipeline["store"].list_aux("rollback_request")) == 1
    assert run(pipeline, pl).rollback["request_id"] == rid
    n = len(upstream.posts)
    run(pipeline, pl)
    assert len(upstream.posts) == n                                      # 영수증이 있으면 다시 보내지 않는다


def test_slow_gate_within_timeout_succeeds(pipeline, upstream):
    seed(pipeline)
    upstream.mode["/v1/rollback"] = "slow"
    rep = run(pipeline, planner(pipeline, upstream))
    assert rep.rollback["receipt_status"] == "APPLIED" and not rep.errors


def test_duplicate_evaluation_makes_one_request_and_one_metric(pipeline, upstream):
    seed(pipeline)
    pl = planner(pipeline, upstream)
    for _ in range(3):
        run(pipeline, pl)
    assert len(pipeline["store"].list_aux("rollback_request")) == 1
    assert len(pipeline["store"].list_aux("metric")) == 1                 # 실행마다 지표 행이 쌓이지 않는다
    assert len(upstream.posts) == 1


def test_lost_receipt_resends_same_request_id(pipeline, upstream):
    """게이트는 처리했지만 응답을 못 받은 경우(멈춤): 다음 실행이 같은 request_id를 다시 보낸다. 중복 처리 방지는 게이트의 request_id 멱등에 달려 있다."""
    seed(pipeline)
    pl = planner(pipeline, upstream)
    upstream.mode["/v1/rollback"] = "hang"
    run(pipeline, pl)
    upstream.mode.clear()
    run(pipeline, pl)
    assert len(upstream.posts) == 2 and upstream.posts[0] == upstream.posts[1]


def test_auto_submit_false_never_calls_gate(pipeline, upstream):
    seed(pipeline)
    pol = dict(POLICY, auto_submit=False)
    pl = RollbackPlanner(store=pipeline["store"], log=pipeline["log"], release_reader=FakeReleaseReader(["rel-0002", "rel-0001"], "rel-0002"),
                         gate=HttpGateService(upstream.url, "t", timeout=1), policy=pol, clock=lambda: NOW)
    rep = run(pipeline, pl)
    assert rep.rollback["action"] == "REQUEST_CREATED" and upstream.posts == [] and not rep.errors


# ---------- D2: I4 읽기 ----------

@pytest.mark.parametrize("mode,code", [("hang", "UNAVAILABLE"), ("500", "UNAVAILABLE"), ("badjson", "UNAVAILABLE"),
                                       ("401", "PERMISSION_DENIED"), ("403", "PERMISSION_DENIED"), ("shape", "INTEGRITY_ERROR")])
def test_release_read_fault_is_bounded_visible_and_does_not_lose_cases(pipeline, upstream, mode, code):
    seed(pipeline)
    upstream.mode["/v1/active-pointer"] = mode
    pl = planner(pipeline, upstream, reader=HttpReleaseReader(upstream.url, "t", timeout=1))
    t0 = time.monotonic()
    rep = run(pipeline, pl)
    assert time.monotonic() - t0 < 3.5
    assert rep.rollback == {"action": "ERROR", "error": code} and {"stage": "rollback", "error": code} in rep.errors
    assert rep.cases_published == 3                                       # I4가 죽어도 사례 게시는 끝났다
    assert upstream.posts == []                                           # 읽지 못하면 롤백 요청도 만들지 않는다
    upstream.mode.clear()
    # 정상 복구 뒤에는 포인터를 읽는다(이 가짜는 list_releases를 주지 않으므로 이어지는 단계는 보지 않는다)
    assert pl._rr.get_active_pointer()["release_id"] == "rel-0002"


def test_rollback_evaluation_has_overall_deadline(pipeline):
    """클라이언트 읽기 timeout이 호출 한 번의 대기라, 평가 전체에도 상한이 있다."""
    seed(pipeline)

    class Stuck(FakeReleaseReader):
        def get_active_pointer(self):
            time.sleep(30)

    pl = RollbackPlanner(store=pipeline["store"], log=pipeline["log"], release_reader=Stuck(["rel-0002"], "rel-0002"), gate=None, policy=POLICY, clock=lambda: NOW)
    cfg = dict(pipeline["cfg"], rollback={"deadline_seconds": 0.5})
    t0 = time.monotonic()
    rep = run(pipeline, pl, cfg=cfg)
    assert time.monotonic() - t0 < 4 and rep.rollback == {"action": "ERROR", "error": "DEADLINE_EXCEEDED"} and rep.errors


def test_release_read_down_for_question_set_is_tried_once_per_run(pipeline):
    """I4가 멈춰 있으면 릴리스마다 timeout을 기다리지 않는다(첫 실패 뒤 이번 실행에서는 더 부르지 않는다)."""
    for i, rel in enumerate(["rel-a", "rel-b", "rel-c"]):
        pipeline["log"].send(envelope(pii_trace("s", f"f{i}"), release_id=rel, sent_at=NOW))

    class Down:
        calls = 0

        def get_release(self, rid):
            Down.calls += 1
            raise BoundaryError("UNAVAILABLE", "x")

    rep = run_once(cfg=pipeline["cfg"], log=pipeline["log"], store=pipeline["store"], deid=pipeline["deid"], detectors=pipeline["detectors"],
                   classifier=pipeline["classifier"], clock=lambda: NOW, release_reader=Down())
    assert Down.calls == 1 and rep.cases_published == 3


# ---------- 잡 중단: 반쯤 쓴 상태가 남지 않는다 ----------

@pytest.mark.parametrize("where", ["after_publish_before_mark", "before_publish"])
def test_job_killed_midway_leaves_no_half_state(pipeline, where):
    seed(pipeline, fails=2, oks=1)
    store = pipeline["store"]
    orig = store.publish_case
    calls = {"n": 0}

    def boom(c):
        calls["n"] += 1
        if calls["n"] == 1 and where == "before_publish":
            raise RuntimeError("killed")
        r = orig(c)
        if calls["n"] == 1:
            raise RuntimeError("killed")
        return r

    store.publish_case = boom
    with pytest.raises(RuntimeError):
        run(pipeline, None)
    store.publish_case = orig
    rep = run(pipeline, None)
    cases = store.list_cases(latest_only=False, limit=500)[0]
    assert len(cases) == 2 and len({c["case_id"] for c in cases}) == 2          # 이중 게시 없음, 빠진 것 없음
    assert all(store.get_trace(c["deidentified_trace_ref"]) for c in cases)    # 사례는 항상 비식별 trace를 가리킨다
    assert rep.cases_published + rep.cases_existing == 2
    assert run(pipeline, None).processed == 0                                  # 모두 표시됐다
    assert [c["version"] for c in cases] == [1, 1]


# ---------- A6: 수집 서버 쪽 멱등 ----------

def test_concurrent_duplicate_trace_is_recorded_once(log_store):
    env = envelope(make_trace("s-d", "t-d"))
    out, errs = [], []

    def go():
        try:
            out.append(log_store.send(copy.deepcopy(env))["status"])
        except Exception as e:      # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=go) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errs and sorted(out) == ["ACCEPTED"] + ["DUPLICATE"] * 7
    assert log_store.counts_by_release() == {"rel-0002": 1}


def test_resent_turn_with_different_content_keeps_first(log_store):
    a = envelope(make_trace("s-d", "t-1"))
    b = envelope(make_trace("s-d", "t-1"), release_id="rel-0003")      # 같은 (session_id, turn_id), 다른 내용
    assert log_store.send(a)["status"] == "ACCEPTED"
    r = log_store.send(b)
    assert r["status"] == "DUPLICATE"
    (row,) = log_store.grant_deidentifier_view().read_unprocessed()
    assert row["envelope"] == a                                                # 먼저 받은 것이 남는다(덮어쓰지 않는다)


def test_invalid_envelope_writes_nothing(log_store):
    bad = envelope(make_trace("s-d", "t-bad"))
    del bad["release_id"]
    with pytest.raises(BoundaryError):
        log_store.send(bad)
    assert log_store.counts_by_release() == {}


# ---------- D6: 검토 게시 경합 ----------

@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_stale_check_cannot_report_success_for_a_lost_write(backend, tmp_path, request):
    """두 프로세스(검토자 둘, 또는 서버와 잡)가 같은 version을 동시에 게시하면 먼저 쓴 쪽이 남고, 진 쪽은 CONFLICT를 받는다(성공으로 말하지 않는다)."""
    from conftest import _pg_schema_url
    if backend == "sqlite":
        mk = lambda: FailureStoreImpl.sqlite(tmp_path / "f.db")     # noqa: E731
    else:
        url = _pg_schema_url(request)
        mk = lambda: FailureStoreImpl.postgres(url)                  # noqa: E731
    a, b = mk(), mk()
    a.put_trace("tr-1", TRACE)
    a.publish_case(mk_case())
    stale = b._b.versions("fc-1")                                   # b가 확인한 시점의 상태(a의 v2 이전)
    a.publish_case(mk_case(version=2, review_status="CONFIRMED", expected={"items": []}))
    real = b._b.versions
    seen = {"n": 0}

    def versions(cid):
        seen["n"] += 1
        return stale if seen["n"] == 1 else real(cid)               # 첫 확인은 낡은 값, 이후는 실제 값

    b._b.versions = versions
    with pytest.raises(BoundaryError) as x:
        b.publish_case(mk_case(version=2, review_status="DISMISSED"))
    assert x.value.code == "CONFLICT"
    assert a.get_case("fc-1")["review_status"] == "CONFIRMED"
    seen["n"] = 0
    b._b.versions = versions
    assert b.publish_case(mk_case(version=2, review_status="CONFIRMED", expected={"items": []}))["review_status"] == "CONFIRMED"   # 같은 내용의 재시도는 성공

"""사람용 요청의 서버 쪽 요청 전체 마감(D120, ADR 0028). 가짜 시계로 시간을 흘리고 실제 시간은 쓰지 않는다."""
import threading

import pytest
from boundary_contracts.errors import BoundaryError

from conftest import envelope, pii_trace
from fakeclock import FakeClock
from ops_feedback.wiring import deadline, pgbase
from ops_feedback.wiring.pipeline import run_once
from ops_feedback.wiring.review_flow import ReviewFlow
from test_postgres_timeout import Conn, FakeTimer, store


class SlowStore:
    """실패 사례 저장소를 감싸 호출마다 가짜 시계를 흘린다(느린 의존)."""

    def __init__(self, inner, clock, cost):
        self.inner, self.clock, self.cost = inner, clock, cost
        self.published = 0

    def get_case(self, *a, **k):
        self.clock.advance(self.cost)
        return self.inner.get_case(*a, **k)

    def publish_case(self, case):
        self.published += 1
        return self.inner.publish_case(case)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def seeded(pipeline):
    pipeline["log"].send(envelope(pii_trace("s-1", "t-1"), release_id="rel-0002"))
    run_once(cfg=pipeline["cfg"], log=pipeline["log"], store=pipeline["store"], deid=pipeline["deid"], detector=pipeline["detector"],
             pseudonymize=pipeline["pseudonymize"], classifier=pipeline["classifier"], clock=pipeline["clock"], evaluate_rollback=False)
    (c,), _ = pipeline["store"].list_cases()
    return c["case_id"]


def review(flow, cid, clock, seconds=9.0):
    with deadline.scope(deadline.Deadline(clock, seconds)):
        return flow.review_with_status(cid, "DISMISSED", "reviewer", reviewed_by="kim1")


def test_review_completes_just_before_the_deadline(pipeline):
    cid = seeded(pipeline)
    clock = FakeClock()
    flow = ReviewFlow(SlowStore(pipeline["store"], clock, 8.9))      # 첫 단계가 8.9초 걸린다고 시계가 말한다: 아직 0.1초 남았다
    rec, created = review(flow, cid, clock)
    assert created and rec["version"] == 2


def test_review_past_the_deadline_stops_without_writing_and_the_same_request_is_safe_to_resend(pipeline):
    cid = seeded(pipeline)
    clock = FakeClock()
    slow = SlowStore(pipeline["store"], clock, 9.5)            # 첫 단계가 9.5초: 마감을 넘었다
    with pytest.raises(BoundaryError) as e:
        review(ReviewFlow(slow), cid, clock)
    assert e.value.code == "UNAVAILABLE" and e.value.retryable
    assert slow.published == 0 and pipeline["store"].get_case(cid)["version"] == 1      # 쓰기 전에 멈췄다: 반쯤 쓴 상태가 없다
    fast = SlowStore(pipeline["store"], FakeClock(), 0.0)      # 같은 요청을 다시 보내면(의존이 정상) 한 번만 반영된다
    rec, created = review(ReviewFlow(fast), cid, FakeClock())
    assert created and rec["version"] == 2
    rec2, created2 = review(ReviewFlow(fast), cid, FakeClock())
    assert not created2 and rec2["version"] == 2


def test_db_call_limit_is_the_remaining_time_when_it_is_shorter_than_the_fixed_limit():
    FakeTimer.made.clear()
    s = store(Conn())
    s.TIMER = FakeTimer
    clock = FakeClock()
    with deadline.scope(deadline.Deadline(clock, 9.0)):
        clock.advance(6.5)                                     # 남은 2.5초 < 고정 상한 statement 3+1초
        s._x(lambda: 1)
        assert FakeTimer.made[-1].secs == 2.5
    s._x(lambda: 1)                                           # 마감이 없는 호출(수집·잡)은 고정 상한 그대로
    assert FakeTimer.made[-1].secs == 4.0
    with deadline.scope(deadline.Deadline(clock, 9.0)):
        s._x(lambda: 1)                                       # 남은 시간이 더 길면 고정 상한이 상한이다
        assert FakeTimer.made[-1].secs == 4.0


def test_no_db_call_starts_after_the_deadline():
    s = store(Conn())
    s.TIMER = FakeTimer
    clock = FakeClock()
    calls = []
    with deadline.scope(deadline.Deadline(clock, 1.0)):
        clock.advance(1.0)
        with pytest.raises(BoundaryError) as e:
            s._x(lambda: calls.append(1))
    assert e.value.code == "UNAVAILABLE" and calls == []


def test_http_review_past_the_deadline_is_503_unavailable_and_retryable(pipeline):
    """HTTP 면: 마감을 넘기면 503 UNAVAILABLE(retryable)이고 사례는 그대로, 의존이 정상이면 같은 요청이 한 번만 반영된다."""
    import io
    import json
    from ops_feedback.components import link_ingest_api as api
    from ops_feedback.wiring.ingest_service import IngestService
    cid = seeded(pipeline)
    clock = FakeClock()
    svc = IngestService(__import__("ops_feedback.wiring.log_store", fromlist=["LogStore"]).LogStore.memory(), SlowStore(pipeline["store"], clock, 9.5))
    app = api.make_app(svc, "ing", read_tokens={"rt": "r"}, review_tokens={"vt": "reviewer"}, require_reviewer_identity=False, clock=clock, request_deadline=9.0)

    def post():
        got = {}
        body = json.dumps({"review_status": "DISMISSED"}).encode()
        env = {"REQUEST_METHOD": "POST", "PATH_INFO": f"/v1/cases/{cid}/review", "CONTENT_LENGTH": str(len(body)), "HTTP_AUTHORIZATION": "Bearer vt",
               "CONTENT_TYPE": "application/json", "wsgi.input": io.BytesIO(body)}
        out = b"".join(app(env, lambda st, h, *a: got.setdefault("st", st)))
        return got["st"], json.loads(out)
    st, rec = post()
    assert st.startswith("503") and rec["code"] == "UNAVAILABLE" and rec["retryable"] is True
    assert pipeline["store"].get_case(cid)["version"] == 1

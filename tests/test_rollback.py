import pytest

from fakes import FakeGate, FakeReleaseReader
from ops_feedback.components.failure_case_store import FailureCaseStore as FailureStoreImpl
from ops_feedback.wiring.log_store import LogStore
from ops_feedback.wiring.rollback_flow import RollbackPlanner
from conftest import confirm_all, envelope, make_trace, pii_trace
from ops_feedback.wiring.pipeline import run_once

POLICY = {"window_hours": 24, "min_turns": 4, "max_failure_rate": 0.2, "counted_review_statuses": ["CONFIRMED"],
          "requested_by_role": "ops-feedback", "auto_submit": True}
NOW = "2026-10-15T00:00:00Z"


def build(pipeline, fail_n, ok_n, reader=None, gate=None, policy=POLICY):
    for i in range(fail_n):
        pipeline["log"].send(envelope(pii_trace("s", f"f{i}"), release_id="rel-0002", sent_at=NOW))
    for i in range(ok_n):
        pipeline["log"].send(envelope(make_trace("s", f"o{i}"), release_id="rel-0002", sent_at=NOW))
    run_once(cfg=pipeline["cfg"], log=pipeline["log"], store=pipeline["store"], deid=pipeline["deid"],
             detector=pipeline["detector"], pseudonymize=pipeline["pseudonymize"], classifier=pipeline["classifier"], clock=lambda: "2026-10-14T12:00:00Z",
             evaluate_rollback=False)
    confirm_all(pipeline["store"])          # 롤백 근거는 사람이 CONFIRMED로 검토한 사례만 센다(L07)
    return RollbackPlanner(store=pipeline["store"], log=pipeline["log"], release_reader=reader or FakeReleaseReader(["rel-0002", "rel-0001"], "rel-0002"),
                           gate=gate, policy=policy, clock=lambda: NOW)


def test_request_created_with_evidence_and_submitted(pipeline):
    gate = FakeGate()
    out = build(pipeline, 3, 3, gate=gate).evaluate()
    assert out["action"] == "REQUEST_CREATED" and out["receipt_status"] == "APPLIED"
    (req,) = gate.calls
    assert (req["from_release_id"], req["to_release_id"]) == ("rel-0002", "rel-0001")
    assert req["evidence_refs"] and req["requested_by_role"] == "ops-feedback"
    metric = pipeline["store"].get_aux("metric", req["evidence_refs"][0])
    assert metric["total_turns"] == 6 and metric["failure_cases"] == 3


def test_idempotent_request_id_and_no_resubmit(pipeline):
    gate = FakeGate()
    p = build(pipeline, 3, 3, gate=gate)
    a, b = p.evaluate(), p.evaluate()
    assert a["request_id"] == b["request_id"] and len(gate.calls) == 1


def test_unavailable_gate_retries_same_request(pipeline):
    gate = FakeGate(fail_times=1)
    p = build(pipeline, 3, 3, gate=gate)
    assert p.evaluate()["submit_error"] == "UNAVAILABLE"
    out = p.evaluate()
    assert out["receipt_status"] == "APPLIED" and len({c["request_id"] for c in gate.calls}) == 1


def test_below_threshold_or_small_sample_no_request(pipeline):
    assert build(pipeline, 1, 9, gate=FakeGate()).evaluate()["reason"] == "기준 이하"


def test_small_sample(pipeline):
    assert build(pipeline, 1, 1, gate=FakeGate()).evaluate()["reason"] == "표본 부족"


def test_no_previous_release(pipeline):
    r = FakeReleaseReader(["rel-0002"], "rel-0002")
    assert build(pipeline, 3, 3, reader=r, gate=FakeGate()).evaluate()["reason"] == "되돌릴 릴리스 없음"


def test_without_auto_submit_only_records(pipeline):
    gate = FakeGate()
    out = build(pipeline, 3, 3, gate=gate, policy={**POLICY, "auto_submit": False}).evaluate()
    assert out["action"] == "REQUEST_CREATED" and gate.calls == []


# ---------- L07: 위조 Trace가 롤백 요청을 만들 수 없다 ----------

def _forged(pipeline, gate):
    """수집 토큰만 있으면 누구나 넣을 수 있는 Trace를 모두 실패로 만들어 잡만 돌린다(사람 검토 없음)."""
    for i in range(8):
        pipeline["log"].send(envelope(pii_trace("forged", f"f{i}"), release_id="rel-0002", sent_at=NOW))
    run_once(cfg=pipeline["cfg"], log=pipeline["log"], store=pipeline["store"], deid=pipeline["deid"], detector=pipeline["detector"],
             pseudonymize=pipeline["pseudonymize"], classifier=pipeline["classifier"], clock=lambda: "2026-10-14T12:00:00Z", evaluate_rollback=False)
    return RollbackPlanner(store=pipeline["store"], log=pipeline["log"], release_reader=FakeReleaseReader(["rel-0002", "rel-0001"], "rel-0002"),
                           gate=gate, policy=POLICY, clock=lambda: NOW)


def test_unreviewed_detections_never_request_a_rollback(pipeline):
    gate = FakeGate()
    out = _forged(pipeline, gate).evaluate()
    assert out["action"] == "NONE" and out["failures"] == 0 and gate.calls == []        # 실패율 100%처럼 보이는 PENDING 사례는 세지 않는다
    assert pipeline["store"].get_aux("rollback_request", "anything") is None


def test_only_human_confirmed_cases_count_and_dismissed_do_not(pipeline):
    from ops_feedback.wiring.review_flow import ReviewFlow
    gate = FakeGate()
    pl = _forged(pipeline, gate)
    items, _ = pipeline["store"].list_cases(limit=500)
    flow = ReviewFlow(pipeline["store"])
    for c in items[:3]:
        flow.review(c["case_id"], "CONFIRMED", "reviewer")
    for c in items[3:]:
        flow.review(c["case_id"], "DISMISSED", "reviewer")
    out = pl.evaluate()
    assert out["failures"] == 3 and out["action"] == "REQUEST_CREATED" and len(gate.calls) == 1


@pytest.mark.parametrize("statuses", [["PENDING"], ["PENDING", "CONFIRMED"], ["DISMISSED"]])
def test_policy_counting_anything_but_confirmed_is_refused(pipeline, statuses, tmp_path):
    import yaml
    from conftest import base_config
    from ops_feedback.config import load_config
    from ops_feedback.wiring.rollback_flow import RollbackPlanner as RP
    from ops_feedback.components import rollback_assessor
    from boundary_contracts.errors import BoundaryError
    pol = dict(POLICY, counted_review_statuses=statuses)
    cfg = base_config()
    cfg["rollback"] = pol
    p = tmp_path / "job.yaml"
    p.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError, match="JobConfigChecks"):          # 설정 읽기에서 시작을 거부한다
        load_config(p)
    with pytest.raises(BoundaryError):                                # 판단 컴포넌트 입구에서도 거부한다
        rollback_assessor.assess({"now": NOW, "release_id": "rel-0002", "total_turns": 10, "cases": [], "policy": pol})

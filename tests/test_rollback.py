import pytest

from fakes import FakeGate, FakeReleaseReader
from ops_feedback.failurestore import FailureStoreImpl
from ops_feedback.logstore import LogStore
from ops_feedback.rollback import RollbackPlanner
from conftest import envelope, make_trace, pii_trace
from ops_feedback.job import run_once

POLICY = {"window_hours": 24, "min_turns": 4, "max_failure_rate": 0.2, "counted_review_statuses": ["PENDING", "CONFIRMED"],
          "requested_by_role": "ops-feedback", "auto_submit": True}
NOW = "2026-10-15T00:00:00Z"


def build(pipeline, fail_n, ok_n, reader=None, gate=None, policy=POLICY):
    for i in range(fail_n):
        pipeline["log"].send(envelope(pii_trace("s", f"f{i}"), release_id="rel-0002", sent_at=NOW))
    for i in range(ok_n):
        pipeline["log"].send(envelope(make_trace("s", f"o{i}"), release_id="rel-0002", sent_at=NOW))
    run_once(cfg=pipeline["cfg"], log=pipeline["log"], store=pipeline["store"], deid=pipeline["deid"],
             detectors=pipeline["detectors"], classifier=pipeline["classifier"], clock=lambda: "2026-10-14T12:00:00Z",
             evaluate_rollback=False)
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

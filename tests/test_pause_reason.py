"""정지 사유 pause_reason(boundary-contracts 2.1.0, CR-H002)의 보존·탐지·지표·구버전 호환. docs/adr/0014."""
import copy

import pytest

from conftest import base_config, envelope, make_trace
from test_detect import ext, log_entry
from ops_feedback.deid import Deidentifier, DeidPolicy
from ops_feedback.detect import RuleDetector, pause_reason_of
from ops_feedback.job import run_once
from ops_feedback.rollback import RollbackPlanner
from fakes import FakeReleaseReader

SIG = {"session_id": "s-1", "write_committed": "COMMITTED", "staff_alert": "DELIVERED"}


def deid(keep=True, **over):
    cfg = base_config()["deid"]
    cfg["keep_pause_reason"] = keep
    cfg.update(over)
    return Deidentifier(DeidPolicy.from_config(cfg), "salt")


def orch(sig):
    return {"turn_kind": None, "session_info_created": None, "log_entry": None, "turn_failure_signal": sig,
            "intervention_result": None, "dispatch_called": False}


def trace_with(reason, where="orchestrator_output"):
    sig = dict(SIG) if reason is None else dict(SIG, pause_reason=reason)
    if where == "log_entry":
        return make_trace(log_entry=log_entry(turn_failure_signal=sig))
    return make_trace(orchestrator_output=orch(sig))


@pytest.mark.parametrize("raw,want", [("RISK", "RISK"), ("REASK_LIMIT", "REASK_LIMIT"), ("OTHER", "OTHER"), ("NEW_VALUE", "OTHER")])
def test_deid_keeps_enum_and_unknown_becomes_other(raw, want):
    out = deid().deidentify(envelope(trace_with(raw))).trace
    assert out["orchestrator_output"]["turn_failure_signal"]["pause_reason"] == want
    assert pause_reason_of(out) == want


def test_unknown_value_text_never_survives():
    out = deid().deidentify(envelope(trace_with("홍길동 010-1234-5678 외"))).trace
    assert out["orchestrator_output"]["turn_failure_signal"]["pause_reason"] == "OTHER"


def test_old_trace_without_field_is_compatible():
    out = deid().deidentify(envelope(trace_with(None))).trace
    assert "pause_reason" not in out["orchestrator_output"]["turn_failure_signal"]
    assert pause_reason_of(out) is None
    (d,) = RuleDetector([{"id": "TURN_FAILURE_SIGNAL"}]).detect(out)
    assert "pause_reason" not in d.detail


def test_policy_off_drops_it_and_fingerprint_differs():
    off, on = deid(False), deid(True)
    assert "pause_reason" not in off.deidentify(envelope(trace_with("RISK"))).trace["orchestrator_output"]["turn_failure_signal"]
    assert off.policy.fingerprint() != on.policy.fingerprint()
    assert deid(False).policy.fingerprint() == DeidPolicy.from_config({**base_config()["deid"]}).fingerprint()   # 안 쓰는 기존 설정의 지문은 그대로


def test_keep_pause_reason_must_be_bool():
    with pytest.raises(ValueError):
        DeidPolicy.from_config({**base_config()["deid"], "keep_pause_reason": "false"})


def test_log_entry_location_also_kept():
    out = deid().deidentify(envelope(trace_with("REASK_LIMIT", "log_entry"))).trace
    assert pause_reason_of(out) == "REASK_LIMIT"


def streak_trace(reason, prior=3):
    log = [{"turn_result": "COMMITTED", "extract_output": ext("REASK")} for _ in range(prior)]
    return make_trace(extract_output=ext("REASK"), session_before={"dialogue_log": log},
                      orchestrator_output=orch(dict(SIG, pause_reason=reason) if reason else dict(SIG)))


RULES = [{"id": "TURN_FAILURE_SIGNAL"}, {"id": "REASK_STREAK", "params": {"threshold": 3}}]


def test_reask_limit_turn_is_one_detection_not_two():
    out = RuleDetector(RULES).detect(streak_trace("REASK_LIMIT"))
    (d,) = out
    assert d.rule_id == "TURN_FAILURE_SIGNAL"
    assert d.detail["pause_reason"] == "REASK_LIMIT" and d.detail["reask_streak"] == 4 and d.detail["service_escalated"] is True


def test_risk_pause_with_streak_stays_two_detections():
    out = RuleDetector(RULES).detect(streak_trace("RISK"))
    assert {d.rule_id for d in out} == {"TURN_FAILURE_SIGNAL", "REASK_STREAK"}
    assert not any(d.detail.get("service_escalated") for d in out)


def test_old_trace_streak_unchanged():
    out = RuleDetector(RULES).detect(streak_trace(None))
    assert {d.rule_id for d in out} == {"TURN_FAILURE_SIGNAL", "REASK_STREAK"}


def test_reask_limit_streak_without_signal_keeps_streak_detection():
    t = streak_trace("REASK_LIMIT")
    t["orchestrator_output"] = orch(None)
    t["log_entry"] = log_entry(turn_failure_signal=dict(SIG, pause_reason="REASK_LIMIT"))
    (d,) = RuleDetector([{"id": "REASK_STREAK", "params": {"threshold": 3}}]).detect(t)
    assert d.detail["service_escalated"] is True


def test_case_and_metrics_carry_reason(pipeline):
    pipeline["deid"] = Deidentifier(DeidPolicy.from_config({**pipeline["cfg"]["deid"], "keep_pause_reason": True}), "test-salt",
                                    pipeline["log"].grant_deidentifier_view())
    now = "2026-10-15T00:00:00Z"
    for i, r in enumerate(["RISK", "REASK_LIMIT", "REASK_LIMIT", None]):
        t = make_trace("s", f"t{i}", orchestrator_output=orch(dict(SIG, pause_reason=r) if r else dict(SIG)))
        pipeline["log"].send(envelope(t, release_id="rel-0002", sent_at=now))
    run_once(cfg=pipeline["cfg"], log=pipeline["log"], store=pipeline["store"], deid=pipeline["deid"], detectors=pipeline["detectors"],
             classifier=pipeline["classifier"], clock=lambda: "2026-10-14T12:00:00Z", evaluate_rollback=False)
    items, _ = pipeline["store"].list_cases(source_release_id="rel-0002", cursor=None, limit=50)
    reasons = sorted(str(((c.get("ext") or {}).get("ops-feedback") or {}).get("pause_reason")) for c in items)
    assert reasons == ["None", "REASK_LIMIT", "REASK_LIMIT", "RISK"]
    pol = {"window_hours": 24, "min_turns": 2, "max_failure_rate": 0.2, "counted_review_statuses": ["PENDING", "CONFIRMED"],
           "requested_by_role": "ops-feedback", "auto_submit": False}
    out = RollbackPlanner(store=pipeline["store"], log=pipeline["log"], release_reader=FakeReleaseReader(["rel-0002", "rel-0001"], "rel-0002"),
                          gate=None, policy=pol, clock=lambda: now).evaluate()
    assert out["by_pause_reason"] == {"REASK_LIMIT": 2, "RISK": 1}         # 사유 없는 사례는 추정해 채우지 않는다
    metric = pipeline["store"].get_aux("metric", out and pipeline["store"].get_aux("rollback_request", out["request_id"])["evidence_refs"][0])
    assert metric["by_pause_reason"] == {"REASK_LIMIT": 2, "RISK": 1} and metric["failure_cases"] == 4


def test_policy_change_does_not_rewrite_existing_case(pipeline):
    """정지 사유 보존을 켜면 정책 지문이 바뀐다. 이미 게시된 PENDING version 1은 덮어쓰지 않고 skipped_diff로 센다(ADR 0013과 같은 원칙, ADR 0014)."""
    now = "2026-10-15T00:00:00Z"
    t = make_trace("s", "t0", orchestrator_output=orch(dict(SIG, pause_reason="RISK")))
    pipeline["log"].send(envelope(t, release_id="rel-0002", sent_at=now))
    kw = dict(cfg=pipeline["cfg"], log=pipeline["log"], store=pipeline["store"], detectors=pipeline["detectors"],
              classifier=pipeline["classifier"], clock=lambda: now, evaluate_rollback=False)
    off = run_once(deid=pipeline["deid"], **kw)                                   # 기본 정책(끔)
    assert off.cases_published == 1
    on_deid = Deidentifier(DeidPolicy.from_config({**pipeline["cfg"]["deid"], "keep_pause_reason": True}), "test-salt",
                           pipeline["log"].grant_deidentifier_view())
    on = run_once(deid=on_deid, reprocess=True, **kw)
    assert (on.cases_published, on.skipped_diff) == (0, 1)
    items, _ = pipeline["store"].list_cases(source_release_id="rel-0002", cursor=None, limit=5)
    assert len(items) == 1 and "pause_reason" not in items[0]["ext"]["ops-feedback"]


@pytest.mark.parametrize("reason", ["RISK", "REASK_LIMIT", "OTHER", None])
def test_ingest_accepts_trace_with_pause_reason_by_survey_schema(log_store, reason):
    """커밋된 survey-service 스키마(f4c3f9b)로 검증하는 수집 경로(I6)가 pause_reason이 든 trace와 옛 trace(필드 없음)를 받는다."""
    sig = dict(SIG, pause_reason=reason) if reason else dict(SIG)
    r = log_store.send(envelope(make_trace("s", "t-ing", orchestrator_output=orch(sig)), release_id="rel-0002"))
    assert r["status"] == "ACCEPTED"


def test_ingest_rejects_value_outside_survey_enum(log_store):
    """스키마의 닫힌 열거 밖 값은 수집이 거부한다(ADR 0014 감수하는 것). 읽기 쪽 OTHER 규약은 수집을 통과한 trace에만 적용된다."""
    from boundary_contracts.errors import BoundaryError
    with pytest.raises(BoundaryError) as e:
        log_store.send(envelope(make_trace("s", "t-bad", orchestrator_output=orch(dict(SIG, pause_reason="NEW"))), release_id="rel-0002"))
    assert e.value.code == "INVALID_RECORD"

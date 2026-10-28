import pytest

from ops_feedback.detect import Classifier, Detection, Detector, RuleDetector, build_detectors
from conftest import make_trace


def test_rules_fire_on_structural_fields():
    d = RuleDetector([{"id": "TURN_FAILURE_SIGNAL"}, {"id": "RESPONSE_FALLBACK"}, {"id": "COMMIT_ERROR"}, {"id": "TURN_FAILED_STEPS"}])
    t = make_trace(response_output="[MASKED]", response_outcome="fallback",
                   persistence_commit_result={"session_id": "p", "status": "DB_ERROR", "write_outcome": "UNKNOWN", "revision": None, "detail": "[MASKED]"},
                   orchestrator_output={"turn_failure_signal": {"session_id": "p", "write_committed": "NOT_COMMITTED", "staff_alert": "NONE"}})
    assert {x.rule_id for x in d.detect(t)} == {"RESPONSE_FALLBACK", "COMMIT_ERROR", "TURN_FAILURE_SIGNAL"}
    assert d.detect(make_trace()) == []


@pytest.mark.parametrize("outcome,fires", [("fallback", True), ("ok", False), ("fixed", False), (None, False)])
def test_generator_fallback_reads_response_outcome(outcome, fires):
    d = RuleDetector([{"id": "RESPONSE_FALLBACK"}])
    assert bool(d.detect(make_trace(response_outcome=outcome))) is fires


@pytest.mark.parametrize("phase,step", [("BEFORE_COMMIT", "LoRA-A"), ("BEFORE_COMMIT", "VALIDATE"), ("BEFORE_COMMIT", "COMMIT"),
                                        ("AFTER_COMMIT", "DELIVER_STAFF"), ("AFTER_COMMIT", "LoRA-B"), ("AFTER_COMMIT", "DELIVER_FRONT")])
def test_failed_steps_with_v2_step_values(phase, step):
    d = RuleDetector([{"id": "TURN_FAILED_STEPS"}])
    t = make_trace(log_entry=log_entry(turn_result={"failed_steps": [{"phase": phase, "step": step}]}))
    (x,) = d.detect(t)
    assert x.detail == {"steps": [f"{phase}:{step}"]}
    assert d.detect(make_trace(log_entry=log_entry(turn_result="COMMITTED"))) == []
    assert d.detect(make_trace(log_entry=log_entry(turn_result="NOT_APPLICABLE"))) == []


def test_failed_steps_survive_deid_unchanged():
    """비식별을 거쳐도 failed_steps(열거값)와 commit 상태는 남아 규칙이 읽는다."""
    from ops_feedback.deid import Deidentifier, DeidPolicy
    from conftest import base_config, envelope
    t = make_trace(log_entry=log_entry(turn_result={"failed_steps": [{"phase": "AFTER_COMMIT", "step": "LoRA-B"}]}),
                   persistence_commit_result={"session_id": "s", "status": "DB_ERROR", "write_outcome": "NOT_APPLIED", "revision": "r", "detail": None},
                   orchestrator_output=None)
    out = Deidentifier(DeidPolicy.from_config(base_config()["deid"]), "salt").deidentify(envelope(t)).trace
    d = RuleDetector([{"id": "TURN_FAILED_STEPS"}, {"id": "COMMIT_ERROR"}])
    assert {x.rule_id for x in d.detect(out)} == {"TURN_FAILED_STEPS", "COMMIT_ERROR"}


def log_entry(**over):
    e = {"input": {"kind": "UTTERANCE", "utterance": "x", "intervention_decision": None}, "handling": {"handled": "UTTERANCE"},
         "question_bank_version": 1, "turn_result": "COMMITTED", "extract_output": None, "validation_violations": None,
         "staff_alert": None, "response_text": None, "staff_alert_delivery": "NOT_APPLICABLE",
         "turn_failure_signal": None, "intervention_result": None}
    e.update(over)
    return e


def ext(action, next_item="Q1", values=None):
    return {"values": [] if values is None else values, "action": action, "next_item": next_item}


V = [{"key": "Q1.a", "value": "[MASKED]", "precision": "exact", "status": "ANSWERED", "save": True}]


@pytest.mark.parametrize("status", ["OUTPUT_INVALID", "VIOLATION", "ERROR"])
def test_extract_failed_by_status(status):
    d = RuleDetector([{"id": "EXTRACT_FAILED"}])
    (x,) = d.detect(make_trace(extract_status=status, extract_attempts=2, extract_output=None))
    assert x.detail == {"reason": "STATUS", "status": status, "attempts": 2}


def test_extract_failed_by_empty_values():
    d = RuleDetector([{"id": "EXTRACT_FAILED"}])
    ok = dict(extract_status="OK", extract_attempts=1)
    assert d.detect(make_trace(extract_output=ext("REDIRECT"), **ok)) == []                    # 값이 비는 것이 정상
    assert d.detect(make_trace(extract_output=ext("NEXT", values=V), **ok)) == []
    assert d.detect(make_trace(extract_output=ext("REASK", values=V), **ok)) == []
    for action in ("NEXT", "REASK", "CONFIRM", "HOLD"):
        (x,) = d.detect(make_trace(extract_output=ext(action), **ok))
        assert x.detail == {"reason": "EMPTY_VALUES"}
    # 비발화 턴(추출 없음)은 해당 없음
    assert d.detect(make_trace()) == []
    assert d.detect(make_trace(extract_status=None, extract_output=None)) == []


def test_extract_failed_empty_ok_actions_configurable():
    d = RuleDetector([{"id": "EXTRACT_FAILED", "params": {"empty_values_ok_actions": ["REDIRECT", "SKIP"]}}])
    ok = dict(extract_status="OK", extract_attempts=1)
    assert d.detect(make_trace(extract_output=ext("SKIP"), **ok)) == []
    assert len(d.detect(make_trace(extract_output=ext("HOLD"), **ok))) == 1


def streak_trace(history, current="REASK", q="Q1"):
    return make_trace(extract_output=ext(current, q, V), extract_status="OK",
                      session_before={"session_info": {"session_id": "p", "question_bank_version": 1}, "answer_records": [],
                                      "progress_record": None, "dialogue_log": [log_entry(extract_output=ext(a, n, V)) for a, n in history],
                                      "dispatch_log": []})


def test_reask_streak_rule_is_enabled_without_params_and_disabled_rule_is_silent():
    # 연속 횟수는 다시 세지 않는다(tests/test_reask_from_trace.py). 규칙은 params 없이 켜지고, 끈 규칙은 아무것도 내지 않는다
    RuleDetector([{"id": "REASK_STREAK"}])
    assert RuleDetector([{"id": "REASK_STREAK", "enabled": False}]).detect(make_trace()) == []


@pytest.mark.parametrize("old,new", [("PARSER_EMPTY", "EXTRACT_FAILED"), ("UNRECOGNIZED_STREAK", "REASK_STREAK")])
def test_retired_rule_names_fail_with_replacement(old, new):
    for enabled in (True, False):
        with pytest.raises(ValueError, match=new):
            RuleDetector([{"id": old, "enabled": enabled, "params": {"threshold": 3}}])


def test_example_config_rules_are_all_known():
    import yaml
    from pathlib import Path
    cfg = yaml.safe_load((Path(__file__).parent.parent / "config/job.example.yaml").read_text("utf-8"))
    ds = build_detectors(cfg["detection"]["detectors"])
    assert ds and ds[0].detect(make_trace()) == []


def test_unknown_rule():
    with pytest.raises(ValueError):
        RuleDetector([{"id": "NOPE"}])


def test_pluggable_detector():
    from tests_plugin import make
    ds = build_detectors([{"factory": "tests_plugin:make"}])
    assert isinstance(ds[0], Detector) and ds[0].detect({})[0].rule_id == "CUSTOM"


def test_classifier_config_driven():
    c = Classifier({"default_failure_type": "UNCLASSIFIED", "rule_to_type": {"B": "TYPE-B"}, "priority": ["A", "B"]})
    assert c.classify([Detection("B"), Detection("Z")]).failure_type == "TYPE-B"
    assert c.classify([Detection("Z")]).failure_type == "UNCLASSIFIED"
    with pytest.raises(ValueError):
        Classifier({})


# --- G14: HOLD·SKIP은 계약상 값이 있어야 한다(ADR 0012) ---
@pytest.mark.parametrize("action", ["HOLD", "SKIP"])
def test_hold_skip_with_values_is_normal_and_without_values_is_failure(action):
    d = RuleDetector([{"id": "EXTRACT_FAILED"}])
    ok = dict(extract_status="OK", extract_attempts=1)
    unknown = [{"key": "Q1.a", "value": None, "precision": "unspecified", "status": "UNKNOWN", "save": False}]
    assert d.detect(make_trace(extract_output=ext(action, values=unknown), **ok)) == []        # 정상 행동은 실패가 아니다
    (x,) = d.detect(make_trace(extract_output=ext(action), **ok))                              # targets가 가리킬 values가 없다 = 계약 위반
    assert x.detail == {"reason": "EMPTY_VALUES"}


def test_example_config_enables_both_rules():
    import yaml
    from pathlib import Path
    cfg = yaml.safe_load((Path(__file__).parent.parent / "config" / "job.example.yaml").read_text("utf-8"))
    rules = {r["id"]: r for r in cfg["detection"]["detectors"][0]["rules"]}
    assert rules["EXTRACT_FAILED"].get("enabled", True) is True
    assert rules["REASK_STREAK"].get("enabled", True) is True
    assert "extract_protections" in cfg["deid"]["keep_count_map_keys"]
    d = RuleDetector([r for r in rules.values()])
    assert not d.detect(streak_trace([("REASK", "Q1")] * 5))                              # 횟수는 다시 세지 않는다: pause_reason이 없으면 사례 없음


def test_retired_generator_fallback_name_is_rejected_with_replacement():
    import pytest
    with pytest.raises(ValueError, match="RESPONSE_FALLBACK"):
        RuleDetector([{"id": "GENERATOR_FALLBACK"}])

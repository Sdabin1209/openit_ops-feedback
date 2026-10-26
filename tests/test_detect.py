import pytest

from ops_feedback.detect import Classifier, Detection, Detector, RuleDetector, build_detectors
from conftest import make_trace


def test_rules_fire_on_structural_fields():
    d = RuleDetector([{"id": "TURN_FAILURE_SIGNAL"}, {"id": "GENERATOR_FALLBACK"}, {"id": "COMMIT_ERROR"}, {"id": "TURN_FAILED_STEPS"}])
    t = make_trace(response_output="[MASKED]", response_outcome="fallback",
                   persistence_commit_result={"session_id": "p", "status": "DB_ERROR", "write_outcome": "UNKNOWN", "revision": None, "detail": "[MASKED]"},
                   orchestrator_output={"turn_failure_signal": {"session_id": "p", "write_committed": "NOT_COMMITTED", "staff_alert": "NONE"}})
    assert {x.rule_id for x in d.detect(t)} == {"GENERATOR_FALLBACK", "COMMIT_ERROR", "TURN_FAILURE_SIGNAL"}
    assert d.detect(make_trace()) == []


@pytest.mark.parametrize("outcome,fires", [("fallback", True), ("ok", False), ("fixed", False), (None, False)])
def test_generator_fallback_reads_response_outcome(outcome, fires):
    d = RuleDetector([{"id": "GENERATOR_FALLBACK"}])
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


def test_reask_streak_counts_consecutive_same_question_including_this_turn():
    d = RuleDetector([{"id": "REASK_STREAK", "params": {"threshold": 3}}])
    assert d.detect(streak_trace([("REASK", "Q1")])) == []                                   # 2번째
    (x,) = d.detect(streak_trace([("REASK", "Q1"), ("REASK", "Q1")]))                        # 3번째
    assert x.detail == {"streak": 3, "action": "REASK"}
    (x,) = d.detect(streak_trace([("NEXT", "Q1"), ("REASK", "Q1"), ("REASK", "Q1"), ("REASK", "Q1")]))
    assert x.detail["streak"] == 4                                                           # 앞의 NEXT에서 끊김
    assert d.detect(streak_trace([("REASK", "Q1"), ("REASK", "Q2")])) == []                   # 문항이 다르면 끊김
    assert d.detect(streak_trace([("REASK", "Q1"), ("NEXT", "Q1"), ("REASK", "Q1")])) == []   # 중간에 다른 action
    assert d.detect(streak_trace([("REASK", "Q1"), ("REASK", "Q1")], current="NEXT")) == []   # 이번 턴이 REASK가 아님


def test_reask_streak_broken_by_turn_without_extraction_and_missing_history():
    d = RuleDetector([{"id": "REASK_STREAK", "params": {"threshold": 3}}])
    t = streak_trace([("REASK", "Q1"), ("REASK", "Q1")])
    t["session_before"]["dialogue_log"].insert(1, log_entry())          # 추출이 없는 턴
    assert d.detect(t) == []
    assert d.detect(make_trace(extract_output=ext("REASK", "Q1", V))) == []      # session_before 없음: 이번 턴만(1)
    assert RuleDetector([{"id": "REASK_STREAK", "params": {"threshold": 1}}]).detect(make_trace(extract_output=ext("REASK", "Q1", V)))


def test_reask_streak_actions_configurable_and_survives_deid():
    d = RuleDetector([{"id": "REASK_STREAK", "params": {"threshold": 2, "actions": ["REASK", "CONFIRM"]}}])
    assert d.detect(streak_trace([("CONFIRM", "Q1")], current="CONFIRM"))
    assert not d.detect(streak_trace([("REASK", "Q1")], current="CONFIRM"))         # action이 섞이면 끊김
    # 비식별(action·next_item 보존)을 거친 trace에서도 센다
    from ops_feedback.deid import Deidentifier, DeidPolicy
    from conftest import base_config, envelope
    out = Deidentifier(DeidPolicy.from_config(base_config()["deid"]), "salt").deidentify(
        envelope(streak_trace([("REASK", "Q1"), ("REASK", "Q1")]))).trace
    assert RuleDetector([{"id": "REASK_STREAK", "params": {"threshold": 3}}]).detect(out)


def test_threshold_has_no_default():
    with pytest.raises(ValueError):
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

import pytest

from ops_feedback.detect import Classifier, Detection, Detector, RuleDetector, build_detectors
from conftest import make_trace


def test_rules_fire_on_structural_fields():
    d = RuleDetector([{"id": "TURN_FAILURE_SIGNAL"}, {"id": "GENERATOR_FALLBACK"}, {"id": "COMMIT_ERROR"}, {"id": "TURN_FAILED_STEPS"}])
    t = make_trace(generator_output={"response_text": "[MASKED]", "is_fallback": True},
                   persistence_commit_result={"session_id": "p", "status": "DB_ERROR", "write_outcome": "UNKNOWN", "revision": None, "detail": "[MASKED]"},
                   orchestrator_output={"turn_failure_signal": {"session_id": "p", "write_committed": "NOT_COMMITTED", "staff_alert": "NONE"}})
    assert {x.rule_id for x in d.detect(t)} == {"GENERATOR_FALLBACK", "COMMIT_ERROR", "TURN_FAILURE_SIGNAL"}
    assert d.detect(make_trace()) == []


def test_threshold_has_no_default():
    with pytest.raises(ValueError):
        RuleDetector([{"id": "UNRECOGNIZED_STREAK"}])
    assert RuleDetector([{"id": "UNRECOGNIZED_STREAK", "enabled": False}]).detect(make_trace()) == []


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

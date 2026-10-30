"""OF-02(R7): 되묻기 한도 정지 여부는 survey-service가 판단해 TurnTrace에 남긴다(pause_reason=REASK_LIMIT).
REASK_STREAK 탐지는 연속 횟수를 다시 세지 않고 그 값을 읽는다. 서비스는 이전 턴 3번 뒤 4번째에 정지하므로 사례도 그 턴에 생겨야 한다."""
from conftest import envelope
from ops_feedback.components.failure_detector import RuleDetector
from ops_feedback.wiring.pipeline import run_once
from test_pause_reason import SIG, ext, log_entry, make_trace, orch


def turn(prior: int, pause_reason=None):
    log = [{"turn_result": "COMMITTED", "extract_output": ext("REASK")} for _ in range(prior)]
    kw = {}
    if pause_reason:
        kw["orchestrator_output"] = orch(dict(SIG, pause_reason=pause_reason))
    return make_trace(extract_output=ext("REASK"), session_before={"dialogue_log": log}, **kw)


STREAK_ONLY = [{"id": "REASK_STREAK", "params": {"threshold": 3}}]


def test_case_appears_on_the_turn_the_service_paused():
    (d,) = RuleDetector(STREAK_ONLY).detect(turn(prior=3, pause_reason="REASK_LIMIT"))     # 서비스가 정지한 4번째
    assert d["rule_id"] == "REASK_STREAK" and d["detail"]["pause_reason"] == "REASK_LIMIT"


def test_no_case_before_the_service_pauses():
    assert RuleDetector(STREAK_ONLY).detect(turn(prior=2)) == []                          # 3번째: 서비스가 아직 정지하지 않음(지금 코드는 여기서 걸린다)


def test_long_streak_without_pause_reason_makes_no_case():
    assert RuleDetector(STREAK_ONLY).detect(turn(prior=6)) == []                          # 횟수를 다시 세지 않는다


def test_other_pause_reasons_are_not_reask_limit():
    assert RuleDetector(STREAK_ONLY).detect(turn(prior=3, pause_reason="RISK")) == []


def test_threshold_and_actions_params_are_ignored():
    for p in ({"threshold": 1}, {"threshold": 99, "actions": ["REASK"]}):
        d = RuleDetector([{"id": "REASK_STREAK", "params": p}])
        assert d.detect(turn(prior=3)) == []
        assert d.detect(turn(prior=0, pause_reason="REASK_LIMIT"))


def test_service_pause_with_signal_is_one_detection():
    rules = [{"id": "TURN_FAILURE_SIGNAL"}, *STREAK_ONLY]
    (d,) = RuleDetector(rules).detect(turn(prior=3, pause_reason="REASK_LIMIT"))
    assert d["rule_id"] == "TURN_FAILURE_SIGNAL" and d["detail"]["service_escalated"] is True


# ---- 설정 조합: REASK_STREAK은 비식별이 pause_reason을 남길 때만 뜻이 있다 ----

def _cfg(tmp_path, keep, enabled=True):
    import yaml
    from conftest import base_config
    cfg = base_config()
    cfg["storage"] = {"log_db": str(tmp_path / "l.db"), "failure_db": str(tmp_path / "f.db")}
    cfg["deid"]["keep_pause_reason"] = keep
    cfg["detection"]["detectors"][0]["rules"].append({"id": "REASK_STREAK", "enabled": enabled, "params": {"threshold": 3}})
    p = tmp_path / "job.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


def test_reask_streak_without_keep_pause_reason_is_a_config_error(tmp_path):
    import pytest
    from ops_feedback.config import load_config
    with pytest.raises(ValueError, match="keep_pause_reason"):
        load_config(_cfg(tmp_path, keep=False))


def test_reask_streak_combinations_that_are_fine(tmp_path):
    from ops_feedback.config import load_config
    load_config(_cfg(tmp_path, keep=True))
    load_config(_cfg(tmp_path, keep=False, enabled=False))          # 규칙을 껐으면 정책은 자유


def test_job_exits_2_with_reason_on_that_combination(tmp_path):
    import os
    import subprocess
    import sys
    env = {**os.environ, "OPS_DEID_SALT": "test-salt-0123456789abcdef-0123456789abcdef"}
    r = subprocess.run([sys.executable, "-m", "ops_feedback.job", "--config", str(_cfg(tmp_path, keep=False))], env=env, capture_output=True, text=True)
    assert r.returncode == 2 and "keep_pause_reason" in r.stderr and "Traceback" not in r.stderr

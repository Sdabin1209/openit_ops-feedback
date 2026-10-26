from __future__ import annotations

import copy
import itertools

import pytest

from ops_feedback.detect import Classifier, build_detectors
from ops_feedback.deid import Deidentifier, DeidPolicy
from ops_feedback.failurestore import FailureStoreImpl
from ops_feedback.logstore import LogStore

FAKE_PII = {"name": "홍길동", "phone": "010-1234-5678", "email": "gildong@example.com"}


def make_trace(session_id="s-1", turn_id="t-1", **over) -> dict:
    from ops_feedback.schemas import SCHEMA_ROOT
    import json
    req = json.loads((SCHEMA_ROOT / "survey/v1/TurnTrace.json").read_text())["$defs"]["TurnTrace"]["required"]
    t = {k: None for k in req}
    t.update(session_id=session_id, turn_id=turn_id, contract_version=1, turn_index=0)
    t.update(over)
    return t


def pii_trace(session_id="s-1", turn_id="t-1") -> dict:
    """환자 발화에 이름·전화·이메일이 섞인 한 턴. 생성기가 폴백을 낸 실패 턴이다."""
    utt = f"저는 {FAKE_PII['name']}이고 {FAKE_PII['phone']} 이메일은 {FAKE_PII['email']} 혈압은 120에 80이에요"
    return make_trace(
        session_id, turn_id,
        frontend_message={"session_id": session_id, "kind": "UTTERANCE", "utterance": utt},
        parser_input={"utterance": utt, "last_directive": None, "awaiting_questions": [], "question_names": [],
                      "answered_slots": [], "guide_list": []},
        parser_output={"items": [{"item_no": 0, "target": {"question_id": "q1", "repeat_key": "", "slot_id": "sys"},
                                  "value": "120", "source_text": "혈압은 120에 80", "intent": "ANSWERED_INTENT",
                                  "progress_op": None, "guide_id": None,
                                  "evidence": [{"start": 0, "end": 3, "offset_unit": "unicode_code_point", "text": FAKE_PII["name"]}]}]},
        generator_output={"response_text": f"{FAKE_PII['name']}님 알겠습니다", "is_fallback": True},
    )


def envelope(trace=None, release_id="rel-0002", sent_at="2026-10-14T00:00:00Z") -> dict:
    trace = trace or make_trace()
    return {"pipeline_schema_version": "2.0", "release_id": release_id, "runtime_contract_version": trace["contract_version"],
            "session_id": trace["session_id"], "turn_id": trace["turn_id"], "sent_at": sent_at, "trace": trace}


def base_config() -> dict:
    return {
        "storage": {"log_db": ":memory:", "failure_db": ":memory:"},
        "deid": {"policy_id": "test-policy", "pseudonymize_keys": ["session_id", "turn_id"],
                 "keep_string_keys": ["question_id", "slot_id", "repeat_key", "guide_id", "pending_id", "code", "category"],
                 "unlisted_string_action": "mask", "mask_defs": ["Value"], "drop_defs": ["QuestionBank"],
                 "residual_patterns": {"email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                                       "phone_kr": r"0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}"}},
        "detection": {"detectors": [{"type": "rule", "rules": [{"id": "TURN_FAILURE_SIGNAL"}, {"id": "GENERATOR_FALLBACK"},
                                                                {"id": "COMMIT_ERROR"}]}]},
        "classification": {"default_failure_type": "UNCLASSIFIED", "rule_to_type": {"GENERATOR_FALLBACK": "T-FALLBACK"},
                           "priority": ["TURN_FAILURE_SIGNAL", "GENERATOR_FALLBACK"]},
        "retention": {"raw_days": None},
    }


class Ticker:
    def __init__(self, start=0):
        self.n = start

    def __call__(self):
        self.n += 1
        return f"2026-10-14T00:00:{self.n % 60:02d}Z"


@pytest.fixture
def cfg():
    return base_config()


@pytest.fixture
def pipeline(cfg):
    log = LogStore.memory(clock=Ticker())
    store = FailureStoreImpl.memory()
    deid = Deidentifier(DeidPolicy.from_config(cfg["deid"]), "test-salt", log.grant_deidentifier_view())
    return dict(cfg=cfg, log=log, store=store, deid=deid, detectors=build_detectors(cfg["detection"]["detectors"]),
                classifier=Classifier(cfg["classification"]), clock=lambda: "2026-10-15T00:00:00Z")


@pytest.fixture(params=["memory", "sqlite"])
def log_store(request, tmp_path):
    t = Ticker()
    return LogStore.memory(clock=t) if request.param == "memory" else LogStore.sqlite(tmp_path / "log.db", clock=t)


@pytest.fixture(params=["memory", "sqlite"])
def failure_store(request, tmp_path):
    return FailureStoreImpl.memory() if request.param == "memory" else FailureStoreImpl.sqlite(tmp_path / "f.db")

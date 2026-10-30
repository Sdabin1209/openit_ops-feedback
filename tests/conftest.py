from __future__ import annotations

from boundary_contracts import PIPELINE_SCHEMA_VERSION

import copy
import itertools

import pytest

from ops_feedback.components.deidentifier import DeidPolicy
from tdeid import Deidentifier
from ops_feedback.components.failure_case_store import FailureCaseStore as FailureStoreImpl
from ops_feedback.components.failure_classifier import FailureClassifier
from ops_feedback.components.failure_detector import FailureDetector
from ops_feedback.wiring.build import make_pseudonymizer
from ops_feedback.wiring.log_store import LogStore

SALT = "test-salt-0123456789abcdef-0123456789abcdef"      # 32바이트 이상(ADR 0017)
FAKE_PII = {"name": "홍길동", "phone": "010-1234-5678", "email": "gildong@example.com"}


def make_trace(session_id="s-1", turn_id="t-1", **over) -> dict:
    import survey_schema
    req = survey_schema.load_schema("TurnTrace", 1)["$defs"]["TurnTrace"]["required"]
    t = {k: None for k in req}
    t.update(session_id=session_id, turn_id=turn_id, contract_version=1, turn_index=0)
    t.update(over)
    return t


def pii_trace(session_id="s-1", turn_id="t-1") -> dict:
    """환자 발화에 이름·전화·이메일이 섞인 한 턴. LoRA-B가 서버 대체 문장을 낸 실패 턴이다."""
    utt = f"저는 {FAKE_PII['name']}이고 {FAKE_PII['phone']} 이메일은 {FAKE_PII['email']} 혈압은 120에 80이에요"
    return make_trace(
        session_id, turn_id,
        frontend_message={"session_id": session_id, "kind": "UTTERANCE", "utterance": utt},
        extract_input={"question_id": "Q1", "current_question": "혈압이 어떻게 되시나요?", "user_utterance": utt,
                       "state": {"Q1": {"sys": {"value": 120, "status": "ANSWERED"}}}},
        extract_output={"values": [{"key": "Q1.sys", "value": 120, "precision": "exact", "status": "ANSWERED", "save": True}],
                        "action": "NEXT", "next_item": "Q2", "reason": f"{FAKE_PII['name']}님 혈압", "targets": [], "note": utt},
        extract_attempts=1, extract_status="OK", extract_protections={"R1": 1},
        validation_ok=True, validation_violations=[],
        response_input={"action": "NEXT", "topic": "혈압", "next": {"id": "Q2", "text": f"{FAKE_PII['name']}님 다음 질문"}},
        response_output=f"{FAKE_PII['name']}님 알겠습니다", response_outcome="fallback", response_protections={"R2": 2},
    )


def envelope(trace=None, release_id="rel-0002", sent_at="2026-10-14T00:00:00Z") -> dict:
    trace = trace or make_trace()
    return {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "release_id": release_id, "runtime_contract_version": trace["contract_version"],
            "session_id": trace["session_id"], "turn_id": trace["turn_id"], "sent_at": sent_at, "trace": trace}


def base_config() -> dict:
    return {
        "storage": {"log_db": ":memory:", "failure_db": ":memory:"},
        "deid": {"policy_id": "test-policy", "pseudonymize_keys": ["session_id", "turn_id"],
                 "keep_string_keys": ["question_id", "slot_id", "repeat_key", "code", "path", "action", "next_item"],
                 "unlisted_string_action": "mask", "mask_defs": ["Value"], "drop_defs": [], "mask_value_keys": ["value"],
                 "residual_patterns": {"email": r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,}",
                                       "phone_kr": r"0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}"}},
        "detection": {"detectors": [{"type": "rule", "rules": [{"id": "TURN_FAILURE_SIGNAL"}, {"id": "RESPONSE_FALLBACK"},
                                                                {"id": "COMMIT_ERROR"}]}]},
        "classification": {"default_failure_type": "UNCLASSIFIED", "rule_to_type": {"RESPONSE_FALLBACK": "T-FALLBACK"},
                           "priority": ["TURN_FAILURE_SIGNAL", "RESPONSE_FALLBACK"]},
        "retention": {"raw_days": "forever", "cases_days": "forever", "metrics_days": "forever"},
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


@pytest.fixture(params=["memory", "sqlite", "postgres"])
def pipeline(cfg, request, tmp_path):
    """잡 전체(수집, 비식별, 탐지, 게시, 보존 삭제)를 세 구현에서 같게 시험한다. 로그·실패 저장소는 같은 종류를 쓴다."""
    t = Ticker()
    if request.param == "memory":
        log, store = LogStore.memory(clock=t), FailureStoreImpl.memory()
    elif request.param == "sqlite":
        log, store = LogStore.sqlite(tmp_path / "l.db", clock=t), FailureStoreImpl.sqlite(tmp_path / "f.db")
    else:
        log, store = LogStore.postgres(_pg_schema_url(request), clock=t), FailureStoreImpl.postgres(_pg_schema_url(request))
    ps = make_pseudonymizer(SALT)
    deid = Deidentifier(DeidPolicy.from_config(cfg["deid"]), SALT, log.grant_deidentifier_view())
    return dict(cfg=cfg, log=log, store=store, deid=deid, pseudonymize=ps, detector=FailureDetector(cfg["detection"]["detectors"]),
                classifier=FailureClassifier(cfg["classification"]), clock=lambda: "2026-10-15T00:00:00Z")


def _pg_schema_url(request):
    """TEST_DATABASE_URL이 있으면 시험마다 새 스키마(search_path)를 만들어 그 URL을 준다. 없으면 SKIP(PASS로 세지 않는다)."""
    import os
    import uuid
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        pytest.skip("TEST_DATABASE_URL 없음: PostgreSQL 시험 건너뜀")
    import psycopg
    schema = "t_" + uuid.uuid4().hex[:12]
    with psycopg.connect(base, autocommit=True) as c:
        c.execute(f"CREATE SCHEMA {schema}")
    request.addfinalizer(lambda: psycopg.connect(base, autocommit=True).execute(f"DROP SCHEMA {schema} CASCADE").connection.close())
    return base + ("&" if "?" in base else "?") + f"options=-csearch_path%3D{schema}"


@pytest.fixture(params=["memory", "sqlite", "postgres"])
def log_store(request, tmp_path):
    t = Ticker()
    if request.param == "memory":
        return LogStore.memory(clock=t)
    if request.param == "sqlite":
        return LogStore.sqlite(tmp_path / "log.db", clock=t)
    return LogStore.postgres(_pg_schema_url(request), clock=t)


@pytest.fixture(params=["memory", "sqlite", "postgres"])
def failure_store(request, tmp_path):
    if request.param == "memory":
        return FailureStoreImpl.memory()
    if request.param == "sqlite":
        return FailureStoreImpl.sqlite(tmp_path / "f.db")
    return FailureStoreImpl.postgres(_pg_schema_url(request))

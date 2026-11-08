"""수집의 session_id/turn_id 교차 검사는 계약 CONVENTIONS 10절 116행의 변환 규칙을 따른다: 봉투 값 == 규칙(trace 원본 값).
Id 형식에 안 맞는 원본(예: token_urlsafe 의 '_'·'-' 시작)은 봉투에 x- 변환값이 실린다. 같음 비교는 그 세션의 모든 Trace 를 거부했다(데모 DEAD 7건)."""
import hashlib

import pytest
from boundary_contracts.errors import BoundaryError

from conftest import envelope, make_trace
from ops_feedback.components import trace_intake
from ops_feedback.wiring.log_store import LogStore


def x_of(original: str) -> str:
    return "x-" + hashlib.sha256(original.encode("utf-8")).hexdigest()[:32]      # 계약 문장을 그대로 옮긴 독립 계산(시험의 기준)


URLSAFE_43 = "_" + "aB3-" * 10 + "xy"           # token_urlsafe(32) 모양: 43자, '_' 로 시작, '-' 포함
DASH_43 = "-" + "Qw_9" * 10 + "ab"
assert len(URLSAFE_43) == len(DASH_43) == 43
LONG_129 = "a" * 129
ID_OK = "s-Abc.1:2_3"                            # Id 형식에 맞으면 그대로


def request(sid_original, tid_original, sid_env, tid_env):
    e = envelope(make_trace(sid_original, tid_original))
    e["session_id"], e["turn_id"] = sid_env, tid_env
    return {"envelope": e, "received_at": "2026-11-08T00:00:00Z", "supported_versions": [1]}


@pytest.mark.parametrize("original,expected", [
    (URLSAFE_43, x_of(URLSAFE_43)),
    (DASH_43, x_of(DASH_43)),
    (ID_OK, ID_OK),
    ("a" * 128, "a" * 128),                      # 경계: 128자는 그대로
    (LONG_129, x_of(LONG_129)),
    ("환자-1", x_of("환자-1")),                   # 비ASCII 는 UTF-8 바이트로 해시한다
])
def test_envelope_id_follows_contract_rule(original, expected):
    assert trace_intake.envelope_id(original) == expected


@pytest.mark.parametrize("sid,tid", [(URLSAFE_43, "t-1"), (DASH_43, "t-1"), (ID_OK, "t-1"), ("s-1", LONG_129), (URLSAFE_43, URLSAFE_43)])
def test_accept_takes_envelope_with_rule_value(sid, tid):
    rec = trace_intake.accept(request(sid, tid, trace_intake.envelope_id(sid), trace_intake.envelope_id(tid)))
    assert rec["session_id"] == (sid if sid in (ID_OK, "s-1") else x_of(sid))


def test_accept_end_to_end_through_log_store():
    log = LogStore.memory()
    e = envelope(make_trace(URLSAFE_43, "t-1"))
    e["session_id"] = x_of(URLSAFE_43)
    assert log.send(e)["status"] == "ACCEPTED"


@pytest.mark.parametrize("sid_env,tid_env", [
    (URLSAFE_43, "t-1"),                          # 변환해야 하는데 원본을 그대로 실음(Id 위반이기도 하다)
    ("x-" + "0" * 32, "t-1"),                     # 규칙과 다른 x- 값
    (x_of(URLSAFE_43)[:-1] + "0", "t-1"),         # 한 글자 다른 해시
    (x_of(URLSAFE_43).upper(), "t-1"),            # 대문자 16진은 규칙(소문자)이 아니다
    (x_of(URLSAFE_43), "t-9"),
])
def test_accept_rejects_value_that_differs_from_rule(sid_env, tid_env):
    with pytest.raises(BoundaryError) as x:
        trace_intake.accept(request(URLSAFE_43, "t-1", sid_env, tid_env))
    assert x.value.code == "INVALID_RECORD"


def test_accept_rejects_x_value_for_original_that_needs_no_conversion():
    with pytest.raises(BoundaryError) as x:
        trace_intake.accept(request("s-1", "t-1", x_of("s-1"), "t-1"))       # Id 형식에 맞는 원본은 그대로 써야 한다
    assert x.value.code == "INVALID_RECORD"


def test_trace_contract_version_is_not_compared_with_the_envelope_d117():
    """D117: trace.contract_version 과 envelope.runtime_contract_version 의 같음은 계약에 없는 거부 조건이다. 봉투 버전의 TurnTrace 스키마를 통과하면 받는다(변이: 옛 비교를 되살리면 실패)."""
    r = request("s-1", "t-1", "s-1", "t-1")
    r["envelope"]["trace"]["contract_version"] = 2        # TurnTrace.contract_version 은 integer(minimum 1)
    assert trace_intake.accept(r)["session_id"] == "s-1"

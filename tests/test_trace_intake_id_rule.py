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
ID_OK = "s-Abc_1-2_3"                           # SessionId(5.0.0: 영문·숫자로 시작, 영문·숫자·_·-, 1~64자)이면 그대로


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


# 문진 스키마 5.0.0 의 TurnTrace 는 session_id 를 계약 SessionId, turn_id 를 계약 Id 로 제한한다. 그래서 Id 형식에 맞지 않는 원본은 trace 에 들어올 수 없고(수집이 계약 검증이 거부한다),
# x- 변환 규칙(envelope_id)을 거치는 원본은 위 표가 보이는 계약 함수 그대로다. 계약 CONVENTIONS 10절은 "원본은 trace 본문에 남는다"고 하지만 문진 스키마 5.0.0 이 그 원본을 받지 않는다: 헤드에게 보고한 어긋남.
@pytest.mark.parametrize("sid,tid", [("s-1", "t-1"), (ID_OK, "t-1"), ("s-1", "a" * 128), ("s-1", "turn.1:2_3-x")])
def test_accept_takes_envelope_with_rule_value(sid, tid):
    rec = trace_intake.accept(request(sid, tid, trace_intake.envelope_id(sid), trace_intake.envelope_id(tid)))
    assert rec["session_id"] == sid and rec["turn_id"] == tid


def test_accept_end_to_end_through_log_store():
    log = LogStore.memory()
    assert log.send(envelope(make_trace("s-1", "t-1")))["status"] == "ACCEPTED"


@pytest.mark.parametrize("sid_env,tid_env", [
    ("s-1", x_of("t-1")),                         # Id 형식에 맞는 원본은 그대로 써야 한다
    ("s-1", "x-" + "0" * 32),                     # 규칙과 다른 x- 값
    ("s-1", "t-1" + "0"),                         # 한 글자 다른 값
    ("s-9", "t-1"),                               # session_id 가 trace 와 다르다
])
def test_accept_rejects_value_that_differs_from_rule(sid_env, tid_env):
    with pytest.raises(BoundaryError) as x:
        trace_intake.accept(request("s-1", "t-1", sid_env, tid_env))
    assert x.value.code == "INVALID_RECORD"


@pytest.mark.parametrize("sid,tid", [(URLSAFE_43, "t-1"), (DASH_43, "t-1"), ("a" * 65, "t-1"), ("s-1", URLSAFE_43), ("s-1", LONG_129)])
def test_original_outside_the_id_types_is_rejected_by_turn_trace_5_0_0(sid, tid):
    """문진 스키마 5.0.0: TurnTrace.session_id(SessionId)·turn_id(Id)에 맞지 않는 원본은 trace 에 실을 수 없어 TurnTrace 위반으로 거부된다(survey 는 token_hex 를 발급해 해당 없음)."""
    with pytest.raises(BoundaryError) as x:
        trace_intake.accept(request(sid, tid, trace_intake.envelope_id(sid), trace_intake.envelope_id(tid)))
    assert x.value.code == "INVALID_RECORD" and "envelope/trace/" in x.value.message      # 입구의 TraceIntakeRequest(계약 TraceEnvelope + 문진 TurnTrace)가 trace 칸을 거부한다


def test_accept_rejects_x_value_for_original_that_needs_no_conversion():
    with pytest.raises(BoundaryError) as x:
        trace_intake.accept(request("s-1", "t-1", x_of("s-1"), "t-1"))       # Id 형식에 맞는 원본은 그대로 써야 한다
    assert x.value.code == "INVALID_RECORD"


def test_trace_contract_version_is_not_compared_with_the_envelope_d117():
    """D117: trace.contract_version 과 envelope.runtime_contract_version 의 같음은 계약에 없는 거부 조건이다. 봉투 버전의 TurnTrace 스키마를 통과하면 받는다(변이: 옛 비교를 되살리면 실패)."""
    r = request("s-1", "t-1", "s-1", "t-1")
    r["envelope"]["trace"]["contract_version"] = 2        # TurnTrace.contract_version 은 integer(minimum 1)
    assert trace_intake.accept(r)["session_id"] == "s-1"


def test_envelope_id_is_the_contracts_function_not_a_local_copy():
    """D117: x- 변환은 계약 boundary_contracts.envelope_id 한 곳(CONVENTIONS 10절, 5.0.0). 이 컴포넌트가 자기 구현을 갖지 않는다."""
    import boundary_contracts
    assert trace_intake.envelope_id is boundary_contracts.envelope_id


def test_session_id_outside_session_id_type_is_rejected_by_the_envelope_schema():
    """5.0.0: 봉투 session_id 는 SessionId(.·: 없음, 64자 이하)다. Id 형식이어도 '.' 가 있으면 envelope_id 가 그대로 두므로 스키마가 거부한다(survey 는 token_hex 를 발급해 해당 없음)."""
    with pytest.raises(BoundaryError) as x:
        trace_intake.accept(request("s-a.b", "t-1", trace_intake.envelope_id("s-a.b"), "t-1"))
    assert x.value.code == "INVALID_RECORD"

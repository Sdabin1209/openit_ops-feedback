"""I6 TraceIngest 적합성: 메모리 구현과 SQLite 구현이 같은 시험을 통과한다."""
import copy

import pytest
from boundary_contracts.errors import BoundaryError
from boundary_contracts.sdk import TraceIngest

from conftest import envelope, make_trace, pii_trace


def test_protocol(log_store):
    assert isinstance(log_store, TraceIngest)


def test_accept_then_duplicate_keeps_first(log_store):
    e1 = envelope(make_trace("s-1", "t-1"))
    r1 = log_store.send(e1)
    assert r1["status"] == "ACCEPTED" and r1["session_id"] == "s-1" and r1["turn_id"] == "t-1"
    e2 = copy.deepcopy(e1)
    e2["release_id"] = "rel-OTHER"       # 같은 키, 다른 내용
    r2 = log_store.send(e2)
    assert r2["status"] == "DUPLICATE"
    assert r2["received_at"] == r1["received_at"]
    # 먼저 받은 것이 유지된다(원본 접근은 비식별 처리기 뷰로만)
    rows = log_store.grant_deidentifier_view().read_unprocessed()
    assert len(rows) == 1 and rows[0]["envelope"]["release_id"] == "rel-0002"


def test_same_turn_other_session_is_distinct(log_store):
    assert log_store.send(envelope(make_trace("s-1", "t-1")))["status"] == "ACCEPTED"
    assert log_store.send(envelope(make_trace("s-2", "t-1")))["status"] == "ACCEPTED"


def test_invalid_envelope(log_store):
    e = envelope()
    del e["release_id"]
    with pytest.raises(BoundaryError) as x:
        log_store.send(e)
    assert x.value.code == "INVALID_RECORD" and not x.value.retryable


def test_invalid_trace_rejected(log_store):
    e = envelope()
    e["trace"] = {"contract_version": 1, "session_id": "s-1", "turn_id": "t-1"}   # 필수 필드 부족
    with pytest.raises(BoundaryError) as x:
        log_store.send(e)
    assert x.value.code == "INVALID_RECORD"
    assert log_store.grant_deidentifier_view().read_unprocessed() == []


def test_trace_envelope_id_mismatch(log_store):
    e = envelope(make_trace("s-1", "t-1"))
    e["turn_id"] = "t-9"
    with pytest.raises(BoundaryError) as x:
        log_store.send(e)
    assert x.value.code == "INVALID_RECORD"


def test_unsupported_runtime_contract_version(log_store):
    e = envelope()
    e["runtime_contract_version"] = 99
    with pytest.raises(BoundaryError) as x:
        log_store.send(e)
    assert x.value.code == "UNSUPPORTED_VERSION"


def test_ext_preserved(log_store):
    e = envelope()
    e["ext"] = {"survey-service": {"k": 1}}
    log_store.send(e)
    assert log_store.grant_deidentifier_view().read_unprocessed()[0]["envelope"]["ext"] == {"survey-service": {"k": 1}}


def test_counts_by_release(log_store):
    log_store.send(envelope(make_trace("s-1", "t-1"), release_id="rel-1"))
    log_store.send(envelope(make_trace("s-1", "t-2"), release_id="rel-1"))
    log_store.send(envelope(make_trace("s-1", "t-3"), release_id="rel-2"))
    assert log_store.counts_by_release() == {"rel-1": 2, "rel-2": 1}

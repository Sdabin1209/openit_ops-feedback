"""I7 FailureStore 적합성: 메모리와 SQLite가 같은 시험을 통과한다."""
import copy
from datetime import datetime, timezone

import pytest
from boundary_contracts import PIPELINE_SCHEMA_VERSION
from boundary_contracts.errors import BoundaryError
from boundary_contracts.sdk import FailureStore

TRACE = {"session_id": "p-x", "turn_id": "p-y"}


def case(cid="fc-1", version=1, **kw):
    c = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "case_id": cid, "version": version, "created_at": "2026-10-15T00:00:00Z",
         "source_release_id": "rel-0001", "runtime_contract_version": 1, "deidentified": True,
         "deidentified_trace_ref": "tr-1", "failure_type": "UNCLASSIFIED", "detection_method": "RULE", "review_status": "PENDING"}
    c.update(kw)
    return c


@pytest.fixture
def store(failure_store):
    failure_store.put_trace("tr-1", TRACE)
    return failure_store


def test_protocol(store):
    assert isinstance(store, FailureStore)


def test_publish_get_idempotent(store):
    assert store.publish_case(case()) == case()
    assert store.publish_case(case()) == case()          # 같은 내용: 성공
    assert store.get_case("fc-1") == case()
    assert len(store.list_cases()[0]) == 1


def test_conflict_on_different_content(store):
    store.publish_case(case())
    with pytest.raises(BoundaryError) as x:
        store.publish_case(case(failure_type="OTHER"))
    assert x.value.code == "CONFLICT"


def test_versions_latest_default_and_explicit(store):
    store.publish_case(case())
    store.publish_case(case(version=2, review_status="CONFIRMED", expected={"items": []}))
    assert store.get_case("fc-1")["version"] == 2
    assert store.get_case("fc-1", version=1)["review_status"] == "PENDING"
    with pytest.raises(BoundaryError) as x:
        store.get_case("fc-1", version=3)
    assert x.value.code == "NOT_FOUND"


def test_version_gap_rejected(store):
    with pytest.raises(BoundaryError) as x:
        store.publish_case(case(version=2))
    assert x.value.code == "FAILED_PRECONDITION"


def test_not_found(store):
    with pytest.raises(BoundaryError) as x:
        store.get_case("nope")
    assert x.value.code == "NOT_FOUND"


def test_invalid_record_not_deidentified(store):
    bad = case()
    bad["deidentified"] = False
    with pytest.raises(BoundaryError) as x:
        store.publish_case(bad)
    assert x.value.code == "INVALID_RECORD"
    miss = case()
    del miss["source_release_id"]
    with pytest.raises(BoundaryError):
        store.publish_case(miss)


def test_trace_must_exist_first(store):
    with pytest.raises(BoundaryError) as x:
        store.publish_case(case("fc-9", deidentified_trace_ref="tr-missing"))
    assert x.value.code == "FAILED_PRECONDITION"


def test_get_trace(store):
    assert store.get_trace("tr-1") == TRACE
    with pytest.raises(BoundaryError) as x:
        store.get_trace("tr-zzz")
    assert x.value.code == "NOT_FOUND"


def test_list_filters_and_latest_only(store):
    store.publish_case(case("fc-1"))
    store.publish_case(case("fc-2", failure_type="B", source_release_id="rel-0002"))
    store.publish_case(case("fc-1", version=2, review_status="CONFIRMED", created_at="2026-10-16T00:00:00Z"))
    items, nxt = store.list_cases()
    assert nxt is None and {(c["case_id"], c["version"]) for c in items} == {("fc-2", 1), ("fc-1", 2)}
    assert len(store.list_cases(latest_only=False)[0]) == 3
    assert [c["case_id"] for c in store.list_cases(review_status="PENDING")[0]] == ["fc-2"]
    assert [c["case_id"] for c in store.list_cases(failure_type="B")[0]] == ["fc-2"]
    assert [c["case_id"] for c in store.list_cases(source_release_id="rel-0002")[0]] == ["fc-2"]
    since = datetime(2026, 10, 16, tzinfo=timezone.utc)
    assert [c["case_id"] for c in store.list_cases(updated_since=since)[0]] == ["fc-1"]


def test_pagination(store):
    for i in range(5):
        store.publish_case(case(f"fc-{i}"))
    seen, cur = [], None
    while True:
        items, cur = store.list_cases(limit=2, cursor=cur)
        seen += [c["case_id"] for c in items]
        if cur is None:
            break
    assert seen == [f"fc-{i}" for i in range(5)]


def test_returned_records_are_copies(store):
    store.publish_case(case())
    store.get_case("fc-1")["failure_type"] = "MUTATED"
    assert store.get_case("fc-1")["failure_type"] == "UNCLASSIFIED"


def test_ext_preserved(store):
    c = case(ext={"model-lab": {"x": 1}})
    store.publish_case(c)
    assert store.get_case("fc-1")["ext"] == {"model-lab": {"x": 1}}

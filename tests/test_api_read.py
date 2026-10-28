"""I7 HTTP 읽기 면 (HTTP.md). 서버를 임시 포트(0)로 띄워 읽는다."""
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from conftest import envelope, make_trace
from ops_feedback.api import make_server, parse_read_tokens
from ops_feedback.failurestore import FailureStoreImpl
from ops_feedback.logstore import LogStore
from test_conformance_failurestore import case


@pytest.fixture
def srv(failure_store):
    failure_store.put_trace("tr-1", {"session_id": "p-x", "turn_id": "p-y"})
    for i in range(3):
        failure_store.publish_case(case(f"fc-{i}", failure_type="A" if i < 2 else "B"))
    failure_store.publish_case(case("fc-0", version=2, review_status="CONFIRMED", created_at="2026-10-16T00:00:00Z"))
    s = make_server(LogStore.memory(), "ingest-tok", "127.0.0.1", 0, failures=failure_store,
                    read_tokens=parse_read_tokens("synthetic-data=r1,model-lab=r2"))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


def call(base, path, token="r1", method="GET", body=None):
    req = urllib.request.Request(base + path, method=method, data=body, headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_get_case_latest_and_version(srv):
    st, c = call(srv, "/v1/cases/fc-0")
    assert st == 200 and c["version"] == 2
    assert call(srv, "/v1/cases/fc-0?version=1")[1]["review_status"] == "PENDING"
    st, e = call(srv, "/v1/cases/nope")
    assert st == 404 and e["code"] == "NOT_FOUND"
    assert call(srv, "/v1/cases/fc-0?version=x")[0] == 400


def test_list_filters_and_paging(srv):
    st, body = call(srv, "/v1/cases")
    assert st == 200 and {c["case_id"] for c in body["items"]} == {"fc-0", "fc-1", "fc-2"} and body["next_cursor"] is None
    assert len(call(srv, "/v1/cases?latest_only=false")[1]["items"]) == 4
    assert [c["case_id"] for c in call(srv, "/v1/cases?failure_type=B")[1]["items"]] == ["fc-2"]
    assert [c["case_id"] for c in call(srv, "/v1/cases?review_status=CONFIRMED")[1]["items"]] == ["fc-0"]
    since = urllib.parse.quote("2026-10-16T00:00:00Z")
    assert [c["case_id"] for c in call(srv, f"/v1/cases?updated_since={since}")[1]["items"]] == ["fc-0"]
    seen, cur = [], None
    while True:
        st, b = call(srv, "/v1/cases?limit=2" + (f"&cursor={cur}" if cur else ""))
        seen += [c["case_id"] for c in b["items"]]
        cur = b["next_cursor"]
        if not cur:
            break
    assert sorted(seen) == ["fc-0", "fc-1", "fc-2"]
    assert call(srv, "/v1/cases?limit=501")[0] == 400
    assert call(srv, "/v1/cases?updated_since=garbage")[0] == 400


def test_get_trace(srv):
    assert call(srv, "/v1/deidentified-traces/tr-1") == (200, {"session_id": "p-x", "turn_id": "p-y"})
    assert call(srv, "/v1/deidentified-traces/zzz")[0] == 404


def test_tokens_are_separated(srv):
    assert call(srv, "/v1/cases", token="r2")[0] == 200            # 다른 역할의 읽기 토큰
    assert call(srv, "/v1/cases", token="ingest-tok")[0] == 403    # 수집 토큰으로 읽기 불가
    assert call(srv, "/v1/cases", token=None)[0] == 401            # D106: 토큰 없음·틀림은 401(권한 없음만 403)
    assert call(srv, "/v1/cases", token="bad")[0] == 401
    body = json.dumps(envelope(make_trace())).encode()
    assert call(srv, "/v1/traces", token="r1", method="POST", body=body)[0] == 403   # 읽기 토큰으로 수집 불가
    assert call(srv, "/v1/traces", token="ingest-tok", method="POST", body=body)[1]["status"] == "ACCEPTED"


def test_publish_not_exposed(srv):
    assert call(srv, "/v1/cases", method="POST", body=b"{}")[0] == 404


def test_response_schema_is_failurecase(srv):
    from ops_feedback.schemas import validate_record
    for c in call(srv, "/v1/cases")[1]["items"]:
        validate_record("FailureCase", c)


def test_read_surface_off_without_store():
    s = make_server(LogStore.memory(), "t", "127.0.0.1", 0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        assert call(f"http://127.0.0.1:{s.server_address[1]}", "/v1/cases", token="t")[0] == 403
    finally:
        s.shutdown()


def test_same_token_rejected():
    with pytest.raises(ValueError):
        make_server(LogStore.memory(), "x", "127.0.0.1", 0, read_tokens={"x": "r"})

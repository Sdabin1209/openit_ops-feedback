import json
import threading
import urllib.request

import pytest
from boundary_contracts.errors import BoundaryError
from boundary_contracts.sdk import TraceIngest

from conftest import envelope, make_trace
from ops_feedback.api import make_server
from boundary_contracts.clients import HttpTraceIngest
from ops_feedback.logstore import LogStore


@pytest.fixture
def server():
    store = LogStore.memory()
    srv = make_server(store, "tok", "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", store
    srv.shutdown()


def get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, json.loads(r.read())


def test_health_ready(server):
    url, _ = server
    assert get(url + "/healthz") == (200, {"status": "ok"})
    st, body = get(url + "/readyz")
    assert st == 200 and body["status"] == "ready"


def test_client_roundtrip_and_errors(server):
    url, store = server
    c = HttpTraceIngest(url, "tok")
    assert isinstance(c, TraceIngest)
    assert c.send(envelope())["status"] == "ACCEPTED"
    assert c.send(envelope())["status"] == "DUPLICATE"
    bad = envelope()
    del bad["release_id"]
    with pytest.raises(BoundaryError) as x:
        c.send(bad)
    assert x.value.code == "INVALID_RECORD"
    with pytest.raises(BoundaryError) as x:
        HttpTraceIngest(url, "wrong").send(envelope())
    assert x.value.code == "PERMISSION_DENIED"


def test_unreachable_is_unavailable():
    with pytest.raises(BoundaryError) as x:
        HttpTraceIngest("http://127.0.0.1:1", "t", timeout=0.5).send(envelope())
    assert x.value.code == "UNAVAILABLE" and x.value.retryable


def test_http_status_table_matches_conventions_9():
    from ops_feedback.api import HTTP_STATUS
    assert HTTP_STATUS == {"INVALID_RECORD": 400, "PERMISSION_DENIED": 403, "NOT_FOUND": 404, "CONFLICT": 409,
                           "FAILED_PRECONDITION": 412, "UNSUPPORTED_VERSION": 422, "INTEGRITY_ERROR": 422, "UNAVAILABLE": 503}


def test_auth_rejection_is_logged_without_token(server, caplog):
    """수집 토큰이 맞지 않으면(FAULT_MATRIX A6 F7) 서버 로그에 코드와 경로가 남고 토큰 값은 남지 않는다."""
    import logging
    url, _ = server
    with caplog.at_level(logging.WARNING):
        with pytest.raises(BoundaryError):
            HttpTraceIngest(url, "wrong-token-canary").send(envelope())
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "PERMISSION_DENIED" in text and "/v1/traces" in text and "wrong-token-canary" not in text

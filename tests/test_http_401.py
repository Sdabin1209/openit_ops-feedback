"""D106(계약 CONVENTIONS.md 6절 보충 규칙): 영역 사이 서비스 API에서 토큰이 없거나 틀리면 401 + `WWW-Authenticate: Bearer`,
토큰은 맞지만 그 호출 권한이 없으면 403. 사람 행위자가 필요한 I7 검토 게시는 `X-Openit-User`가 없으면 401.
오류 본문 code는 어느 쪽이든 PERMISSION_DENIED. /healthz, /readyz는 인증 없음. raw HTTP(http.client)로 확인한다."""
from waiting import HANG_GUARD_SECONDS
import http.client
import json
import threading

import pytest

from ops_feedback.wiring.ingest_service import make_ingest_server as make_server
from ops_feedback.config import parse_read_tokens
from ops_feedback.components.failure_case_store import FailureCaseStore as FailureStoreImpl
from ops_feedback.wiring.log_store import LogStore

ING, RD, RV = "ing-tok", "read-tok", "review-tok"


@pytest.fixture
def port():
    s = make_server(LogStore.memory(), ING, "127.0.0.1", 0, failures=FailureStoreImpl.memory(),
                    read_tokens=parse_read_tokens(f"synthetic-data={RD}"), review_tokens=parse_read_tokens(f"reviewer={RV}"))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s.server_address[1]
    s.shutdown()


def call(port, method, path, auth=None, body=b"{}", extra=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=HANG_GUARD_SECONDS)      # 매달림 감시
    h = dict(extra or {})
    if auth is not None:
        h["Authorization"] = auth
    c.request(method, path, body=body if method == "POST" else None, headers=h)
    r = c.getresponse()
    raw = r.read()
    return r.status, r.getheader("WWW-Authenticate"), (json.loads(raw) if raw else None)


ROUTES = [("POST", "/v1/traces", ING), ("GET", "/v1/cases", RD), ("GET", "/v1/cases/fc-x", RD),
          ("GET", "/v1/deidentified-traces/tr-x", RD), ("POST", "/v1/cases/fc-x/review", RV)]
BAD_CREDENTIALS = [None, "Bearer wrong", "Basic abc", "Bearer ", "Bearer " + "한글".encode("utf-8").decode("latin-1")]


@pytest.mark.parametrize("method,path,_tok", ROUTES)
@pytest.mark.parametrize("auth", BAD_CREDENTIALS, ids=["none", "wrong", "basic", "empty", "nonascii"])
def test_missing_or_wrong_token_is_401_with_www_authenticate(port, method, path, _tok, auth):
    st, www, body = call(port, method, path, auth)
    assert st == 401 and www == "Bearer" and body["code"] == "PERMISSION_DENIED"


@pytest.mark.parametrize("method,path,right", ROUTES)
def test_valid_token_for_another_surface_is_403(port, method, path, right):
    for tok in {ING, RD, RV} - {right}:
        st, _, body = call(port, method, path, f"Bearer {tok}", extra={"X-Openit-User": "kim.reviewer"})
        assert st == 403 and body["code"] == "PERMISSION_DENIED", (path, tok)


def test_review_without_user_header_is_401_but_with_it_passes_auth(port):
    st, www, body = call(port, "POST", "/v1/cases/fc-x/review", f"Bearer {RV}", body=b'{"review_status":"DISMISSED"}')
    assert st == 401 and www == "Bearer" and body["code"] == "PERMISSION_DENIED"
    st, _, body = call(port, "POST", "/v1/cases/fc-x/review", f"Bearer {RV}", body=b'{"review_status":"DISMISSED"}',
                       extra={"X-Openit-User": "kim.reviewer"})
    assert st == 404            # 인증을 지나 사례가 없어서 404


def test_health_endpoints_need_no_auth(port):
    assert call(port, "GET", "/healthz")[0] == 200
    assert call(port, "GET", "/readyz")[0] == 200


def test_read_surface_off_unknown_token_is_401_known_ingest_token_is_403():
    s = make_server(LogStore.memory(), ING, "127.0.0.1", 0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        p = s.server_address[1]
        assert call(p, "GET", "/v1/cases", "Bearer nope")[0] == 401
        assert call(p, "GET", "/v1/cases", f"Bearer {ING}")[0] == 403
    finally:
        s.shutdown()


def test_review_identity_is_checked_before_body_validation(port):
    st, www, _ = call(port, "POST", "/v1/cases/fc-x/review", f"Bearer {RV}", body=b"not json")
    assert st == 401 and www == "Bearer"

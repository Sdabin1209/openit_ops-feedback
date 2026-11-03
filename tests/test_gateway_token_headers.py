"""CONVENTIONS 6절(3.4.5): 신원 헤더 X-Openit-User 는 게이트웨이 토큰(deploy-exposure.json 의 upstream_token 이 가리키는 토큰)이 붙은 요청에서만 쓰고,
서비스에 있는 그 밖의 모든 토큰의 같은 헤더는 무시한다. 신원이 필수인 검토 면에서 무시된 헤더는 신원 없음이므로 401 이다."""
import json
import re
from pathlib import Path

import pytest
from test_http_401 import ING, RD, RV, call, port  # noqa: F401  (port 는 fixture)

import ops_feedback.components.link_ingest_api as api

ROOT = Path(__file__).resolve().parents[1]
BODY = b'{"review_status":"DISMISSED"}'


def test_gateway_roles_are_exactly_the_declared_upstream_tokens():
    """코드의 게이트웨이 역할 집합이 deploy-exposure.json 의 upstream_token(ops-<면>-<역할>)과 같다."""
    ex = json.loads((ROOT / "deploy-exposure.json").read_text("utf-8"))
    declared = {r["upstream_token"] for r in ex["routes"] if "upstream_token" in r}
    derived = set()
    for t in declared:
        m = re.fullmatch(r"ops-(read|review)-([a-z-]+)", t)
        assert m, t
        derived.add(f"{m.group(1)}:{m.group(2)}")
    assert api.GATEWAY_ROLES == derived


def test_gateway_token_header_is_used(port):
    st, _, body = call(port, "POST", "/v1/cases/fc-x/review", f"Bearer {RV}", body=BODY, extra={"X-Openit-User": "kim.reviewer"})
    assert st == 404 and body["code"] == "NOT_FOUND"          # 신원이 인정되어 인증을 지나고 사례가 없어 404


@pytest.mark.parametrize("tok", [ING, RD])
def test_header_with_non_gateway_token_is_ignored_everywhere(port, tok):
    """수집·읽기(비게이트웨이) 토큰은 검토 면에서 403 이고, 헤더가 있어도 신원이 생기지 않는다."""
    st, _, body = call(port, "POST", "/v1/cases/fc-x/review", f"Bearer {tok}", body=BODY, extra={"X-Openit-User": "kim.reviewer"})
    assert st == 403 and body["code"] == "PERMISSION_DENIED"


def test_non_gateway_review_token_cannot_borrow_identity():
    """검토 토큰이라도 게이트웨이 토큰(역할 reviewer)이 아니면 헤더를 무시하므로 신원 필수 설정에서 401."""
    import threading
    from ops_feedback.components.failure_case_store import FailureCaseStore
    from ops_feedback.config import parse_read_tokens
    from ops_feedback.wiring.ingest_service import make_ingest_server
    from ops_feedback.wiring.log_store import LogStore
    s = make_ingest_server(LogStore.memory(), ING, "127.0.0.1", 0, failures=FailureCaseStore.memory(),
                           read_tokens=parse_read_tokens(f"synthetic-data={RD}"), review_tokens=parse_read_tokens("auditor=aud-tok"))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        st, www, body = call(s.server_address[1], "POST", "/v1/cases/fc-x/review", "Bearer aud-tok", body=BODY, extra={"X-Openit-User": "kim.reviewer"})
        assert st == 401 and www == "Bearer" and body["code"] == "PERMISSION_DENIED"
    finally:
        s.shutdown()

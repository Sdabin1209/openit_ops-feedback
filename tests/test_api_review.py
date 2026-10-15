"""POST /v1/cases/{id}/review (HTTP.md I7 검토 게시). 서버를 띄워 HTTP로 검토하고 읽기 면으로 새 version을 확인한다."""
import json
import threading
import urllib.error
import urllib.request

import pytest
from boundary_contracts.clients import HttpFailureStore

from conftest import envelope, pii_trace
from ops_feedback.api import make_server, parse_read_tokens
from ops_feedback.failurestore import FailureStoreImpl
from ops_feedback.job import run_once
from ops_feedback.logstore import LogStore

GOOD_PARSER = {"items": []}
GEN_NOT_IN_TRACE = {"response_text": "x", "is_fallback": False}


@pytest.fixture
def env(pipeline):
    pipeline["log"].send(envelope(pii_trace("s-1", "t-1"), release_id="rel-0002"))
    run_once(cfg=pipeline["cfg"], log=pipeline["log"], store=pipeline["store"], deid=pipeline["deid"],
             detectors=pipeline["detectors"], classifier=pipeline["classifier"], clock=pipeline["clock"])
    (c,), _ = pipeline["store"].list_cases()
    s = make_server(LogStore.memory(), "ingest-tok", "127.0.0.1", 0, failures=pipeline["store"],
                    read_tokens=parse_read_tokens("synthetic-data=read-tok"),
                    review_tokens=parse_read_tokens("reviewer=review-tok"))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}", c["case_id"]
    s.shutdown()


def post(base, cid, body, token="review-tok"):
    req = urllib.request.Request(f"{base}/v1/cases/{cid}/review", method="POST", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_review_creates_version_201_then_idempotent_200_and_visible_via_read(env):
    base, cid = env
    st, rec = post(base, cid, {"review_status": "CONFIRMED", "expected": GOOD_PARSER, "expected_kind": "PARSER"})
    assert st == 201 and rec["version"] == 2 and rec["review_status"] == "CONFIRMED" and rec["expected_kind"] == "PARSER"
    assert rec["ext"]["ops-feedback"]["review"]["reviewer_role"] == "reviewer"
    st2, rec2 = post(base, cid, {"review_status": "CONFIRMED", "expected": GOOD_PARSER, "expected_kind": "PARSER"})
    assert st2 == 200 and rec2 == rec
    fs = HttpFailureStore(base, "read-tok")
    assert fs.get_case(cid)["version"] == 2 and fs.get_case(cid, version=1)["review_status"] == "PENDING"
    assert fs.list_cases(review_status="CONFIRMED")[0][0]["case_id"] == cid


def test_dismiss_and_reopen(env):
    base, cid = env
    assert post(base, cid, {"review_status": "DISMISSED"})[1]["version"] == 2
    st, rec = post(base, cid, {"review_status": "PENDING"})
    assert st == 201 and rec["version"] == 3 and rec["review_status"] == "PENDING"


def test_invalid_expected_cases(env):
    base, cid = env
    st, e = post(base, cid, {"review_status": "CONFIRMED", "expected": GOOD_PARSER})
    assert st == 400 and "expected_kind" in e["message"]
    st, e = post(base, cid, {"review_status": "CONFIRMED", "expected": {"items": "x"}, "expected_kind": "PARSER"})
    assert st == 400 and "ParserOutput" in e["message"]
    st, e = post(base, cid, {"review_status": "CONFIRMED", "expected": GEN_NOT_IN_TRACE, "expected_kind": "GENERATOR"})
    assert st == 400 and "generator_input" in e["message"]
    assert post(base, cid, {"review_status": "DISMISSED", "expected": GOOD_PARSER, "expected_kind": "PARSER"})[0] == 400
    assert post(base, cid, {"review_status": "MAYBE"})[0] == 400
    assert post(base, cid, {"review_status": "CONFIRMED", "extra": 1})[0] == 400
    assert HttpFailureStore(base, "read-tok").get_case(cid)["version"] == 1      # 실패한 검토는 아무것도 남기지 않는다


def test_unknown_case_404(env):
    assert post(env[0], "fc-nope", {"review_status": "DISMISSED"})[0] == 404


def test_token_separation(env):
    base, cid = env
    for tok in ("read-tok", "ingest-tok", "bad"):
        assert post(base, cid, {"review_status": "DISMISSED"}, token=tok)[0] == 403
    req = urllib.request.Request(base + "/v1/cases", headers={"Authorization": "Bearer review-tok"})
    with pytest.raises(urllib.error.HTTPError) as x:
        urllib.request.urlopen(req)
    assert x.value.code == 403
    req = urllib.request.Request(base + "/v1/traces", method="POST", data=b"{}", headers={"Authorization": "Bearer review-tok"})
    with pytest.raises(urllib.error.HTTPError) as x:
        urllib.request.urlopen(req)
    assert x.value.code == 403

"""POST /v1/cases/{id}/review (HTTP.md I7 검토 게시). 서버를 띄워 HTTP로 검토하고 읽기 면으로 새 version을 확인한다."""
import json
import threading
import urllib.error
import urllib.request

import pytest
from boundary_contracts import PIPELINE_SCHEMA_VERSION
from boundary_contracts.clients import HttpFailureStore

from conftest import envelope, pii_trace
from ops_feedback.api import make_server, parse_read_tokens
from ops_feedback.failurestore import FailureStoreImpl
from ops_feedback.job import run_once
from ops_feedback.logstore import LogStore

GOOD_PARSER = {"action": "NEXT", "next_item": "END", "values": []}      # LORA_A 출력(ActionExtraction)
GOOD_TEXT = "혈압약은 지금도 드시고 계신가요?"                          # LORA_B 출력(ResponseText)


@pytest.fixture
def env(pipeline):
    pipeline["log"].send(envelope(pii_trace("s-1", "t-1"), release_id="rel-0002"))
    run_once(cfg=pipeline["cfg"], log=pipeline["log"], store=pipeline["store"], deid=pipeline["deid"],
             detectors=pipeline["detectors"], classifier=pipeline["classifier"], clock=pipeline["clock"])
    (c,), _ = pipeline["store"].list_cases()
    s = make_server(LogStore.memory(), "ingest-tok", "127.0.0.1", 0, failures=pipeline["store"],
                    read_tokens=parse_read_tokens("synthetic-data=read-tok"),
                    review_tokens=parse_read_tokens("reviewer=review-tok"), require_reviewer_identity=False)
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
    st, rec = post(base, cid, {"review_status": "CONFIRMED", "expected": GOOD_PARSER, "expected_kind": "LORA_A"})
    assert st == 201 and rec["version"] == 2 and rec["review_status"] == "CONFIRMED" and rec["expected_kind"] == "LORA_A"
    assert rec["ext"]["ops-feedback"]["review"]["reviewer_role"] == "reviewer"
    st2, rec2 = post(base, cid, {"review_status": "CONFIRMED", "expected": GOOD_PARSER, "expected_kind": "LORA_A"})
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
    st, e = post(base, cid, {"review_status": "CONFIRMED", "expected": {"items": "x"}, "expected_kind": "LORA_A"})
    assert st == 400 and "ActionExtraction" in e["message"]
    st, e = post(base, cid, {"review_status": "CONFIRMED", "expected": GOOD_PARSER, "expected_kind": "LORA_B"})
    assert st == 400 and "ResponseText" in e["message"]            # 객체는 LORA_B의 정답(문자열)이 아니다
    st, e = post(base, cid, {"review_status": "CONFIRMED", "expected": GOOD_TEXT, "expected_kind": "LORA_A"})
    assert st == 400 and "ActionExtraction" in e["message"]
    assert post(base, cid, {"review_status": "CONFIRMED", "expected": GOOD_TEXT, "expected_kind": "PARSER"})[0] == 400   # v1 값
    assert post(base, cid, {"review_status": "CONFIRMED", "expected": 7, "expected_kind": "LORA_B"})[0] == 400
    assert post(base, cid, {"review_status": "DISMISSED", "expected": GOOD_PARSER, "expected_kind": "LORA_A"})[0] == 400
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


GOOD_INPUT = {"user_utterance": "혈압약은 먹고 있어요", "question_id": "Q1.D03", "current_question": "고혈압 약을 복용하시나요?", "state": {}}
GOOD_REQUEST = {"action": "START", "topic": "혈압", "next": {"type": "NEXT", "question_id": "Q1.D01", "text": "첫 질문입니다."}}


def test_reviewed_input_stored_idempotent_and_read_back(env):
    base, cid = env
    body = {"review_status": "CONFIRMED", "expected": GOOD_PARSER, "expected_kind": "LORA_A", "reviewed_input": GOOD_INPUT}
    st, rec = post(base, cid, body)
    assert st == 201 and rec["reviewed_input"] == GOOD_INPUT and rec["pipeline_schema_version"] == PIPELINE_SCHEMA_VERSION
    assert post(base, cid, body) == (200, rec)                                  # 재전송은 같은 결과
    fs = HttpFailureStore(base, "read-tok")
    assert fs.get_case(cid)["reviewed_input"] == GOOD_INPUT
    assert fs.get_case(cid, version=1).get("reviewed_input") is None
    st, rec3 = post(base, cid, dict(body, reviewed_input=dict(GOOD_INPUT, user_utterance="다른 문장")))
    assert st == 201 and rec3["version"] == 3                                   # 다른 입력은 새 version


def test_reviewed_input_without_expected(env):
    base, cid = env
    st, rec = post(base, cid, {"review_status": "CONFIRMED", "expected_kind": "LORA_A", "reviewed_input": GOOD_INPUT})
    assert st == 201 and "expected" not in rec and rec["expected_kind"] == "LORA_A"


def test_reviewed_input_rejections(env):
    base, cid = env
    st, e = post(base, cid, {"review_status": "CONFIRMED", "expected_kind": "LORA_A", "reviewed_input": {"user_utterance": 1}})
    assert st == 400 and "ActionExtractionInput" in e["message"]
    st, e = post(base, cid, {"review_status": "CONFIRMED", "reviewed_input": GOOD_INPUT})
    assert st == 400 and "expected_kind" in e["message"]
    # LORA_B용 입력(ResponseRequest)이 아니다
    assert post(base, cid, {"review_status": "CONFIRMED", "expected_kind": "LORA_B", "reviewed_input": GOOD_INPUT})[0] == 400
    assert post(base, cid, {"review_status": "DISMISSED", "expected_kind": "LORA_A", "reviewed_input": GOOD_INPUT})[0] == 400
    assert post(base, cid, {"review_status": "CONFIRMED", "reviewed_input": "x", "expected_kind": "LORA_A"})[0] == 400
    assert HttpFailureStore(base, "read-tok").get_case(cid)["version"] == 1


def post_h(base, cid, body, user=None, token="review-tok"):
    h = {"Authorization": f"Bearer {token}"}
    if user is not None:
        h["X-Openit-User"] = user
    req = urllib.request.Request(f"{base}/v1/cases/{cid}/review", method="POST", data=json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_reviewed_by_header_and_fallback(env):
    base, cid = env
    st, r2 = post_h(base, cid, {"review_status": "DISMISSED"}, user="dr.kim")
    assert st == 201 and r2["ext"]["ops-feedback"]["reviewed_by"] == "dr.kim"
    st, r3 = post_h(base, cid, {"review_status": "PENDING"})                 # 헤더 없음: 토큰 역할
    assert st == 201 and r3["ext"]["ops-feedback"]["reviewed_by"] == "reviewer"
    st, r4 = post_h(base, cid, {"review_status": "DISMISSED"}, user="nurse:lee")
    assert st == 201 and r4["ext"]["ops-feedback"]["reviewed_by"] == "nurse:lee"


@pytest.mark.parametrize("bad", ["a b", "-x", "x" * 129, "a/b", ""])
def test_invalid_user_header(env, bad):
    base, cid = env
    st, e = post_h(base, cid, {"review_status": "DISMISSED"}, user=bad)
    assert st == 400 and "X-Openit-User" in e["message"]
    assert HttpFailureStore(base, "read-tok").get_case(cid)["version"] == 1


def test_idempotency_criterion(env):
    base, cid = env
    body = {"review_status": "CONFIRMED", "expected": GOOD_PARSER, "expected_kind": "LORA_A", "reviewed_input": GOOD_INPUT}
    st, r1 = post_h(base, cid, body, user="dr.kim")
    assert st == 201
    # 같은 내용을 다른 사람이 보내도 200, 기존 레코드 그대로(reviewed_by 불변)
    st, again = post_h(base, cid, body, user="dr.park")
    assert st == 200 and again == r1 and again["ext"]["ops-feedback"]["reviewed_by"] == "dr.kim"
    assert HttpFailureStore(base, "read-tok").get_case(cid)["version"] == 2
    # 네 가지 중 하나라도 다르면 새 version
    for i, change in enumerate([{"review_status": "DISMISSED"}, {"expected": GOOD_TEXT, "expected_kind": "LORA_B"} ,
                                {"reviewed_input": dict(GOOD_INPUT, user_utterance="다름")}, {"expected": None}]):
        b = dict(body, **change)
        if b.get("expected") is None:
            b.pop("expected")
        if b["review_status"] == "DISMISSED":
            b = {"review_status": "DISMISSED"}
        if change.get("expected_kind") == "LORA_B":
            continue                      # reviewed_input(LORA_A 입력)과 짝이 맞지 않아 400이다. 별도 사례로 확인
        st, rec = post_h(base, cid, b, user="dr.kim")
        assert st == 201, (change, rec)
        assert rec["ext"]["ops-feedback"]["reviewed_by"] == "dr.kim"
        # 바로 재전송하면 200
        assert post_h(base, cid, b, user="dr.lee")[0] == 200
        # 다음 반복이 이전 결과와 다르도록 원래 본문으로 복귀
        assert post_h(base, cid, body, user="dr.kim")[0] == 201


def test_list_response_shape(env):
    base, _ = env
    req = urllib.request.Request(base + "/v1/cases", headers={"Authorization": "Bearer read-tok"})
    with urllib.request.urlopen(req) as r:
        body = json.loads(r.read())
    assert set(body) == {"items", "next_cursor"} and isinstance(body["items"], list) and body["next_cursor"] is None


def test_lora_b_expected_string_and_request_roundtrip(env):
    """2.0.0: LORA_B의 정답은 문자열, 검토 입력은 ResponseRequest. 읽기 면에서도 그대로 나온다."""
    base, cid = env
    body = {"review_status": "CONFIRMED", "expected": GOOD_TEXT, "expected_kind": "LORA_B", "reviewed_input": GOOD_REQUEST}
    st, rec = post(base, cid, body)
    assert st == 201 and rec["expected"] == GOOD_TEXT and rec["expected_kind"] == "LORA_B" and rec["reviewed_input"] == GOOD_REQUEST
    assert post(base, cid, body) == (200, rec)
    assert HttpFailureStore(base, "read-tok").get_case(cid)["expected"] == GOOD_TEXT
    # 같은 expected_kind라도 입력이 LORA_A 형식이면 거부
    assert post(base, cid, dict(body, reviewed_input=GOOD_INPUT))[0] == 400
    # 빈 문자열은 ResponseText(minLength 1) 위반
    assert post(base, cid, dict(body, expected=""))[0] == 400

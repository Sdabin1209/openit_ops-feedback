"""L12(ADR 0030): 환자 발화 두 칸만 검토 사본에 원문으로 보존하고, reviewer 역할만 읽으며, 읽을 때마다 접근 기록을 남긴다."""
import copy
import json
import threading
import urllib.error
import urllib.request

import pytest

from conftest import base_config, envelope, pii_trace
from ops_feedback.components.deidentifier import DeidError, Deidentifier, DeidPolicy
from ops_feedback.components.failure_case_store import FailureCaseStore
from ops_feedback.components.failure_classifier import FailureClassifier
from ops_feedback.components.failure_detector import FailureDetector
from ops_feedback.config import parse_read_tokens
from ops_feedback.wiring.build import make_pseudonymizer
from ops_feedback.wiring.ingest_service import make_ingest_server as make_server
from ops_feedback.wiring.log_store import LogStore
from ops_feedback.wiring.pipeline import run_once

UTT = "혈압약은 아침마다 먹고 있어요"
SALT = "test-salt-0123456789abcdef-0123456789abcdef"


def clean_trace(sid="s-1", tid="t-1", utt=UTT):
    t = pii_trace(sid, tid)
    t["frontend_message"]["utterance"] = utt
    t["extract_input"]["user_utterance"] = utt
    return t


def cfg_with(keep):
    cfg = base_config()
    if keep is not None:
        cfg["deid"]["review_utterance"] = {"keep": keep}
    return cfg


def run(cfg, trace, store=None):
    ps = make_pseudonymizer(SALT)
    log, store = LogStore.memory(clock=lambda: "2026-10-14T00:00:01Z"), store or FailureCaseStore.memory()
    log.send(envelope(trace))
    rep = run_once(cfg=cfg, log=log, store=store, deid=Deidentifier(DeidPolicy.from_config(cfg["deid"]), ps), pseudonymize=ps,
                   detector=FailureDetector(cfg["detection"]["detectors"]), classifier=FailureClassifier(cfg["classification"]),
                   clock=lambda: "2026-10-15T00:00:00Z", evaluate_rollback=False)
    return rep, store


def strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, list):
        for x in v:
            yield from strings(x)
    elif isinstance(v, dict):
        for x in v.values():
            yield from strings(x)


def test_only_the_two_utterance_cells_keep_the_original_and_everything_else_stays_masked():
    rep, store = run(cfg_with(True), clean_trace())
    (c,), _ = store.list_cases()
    ref = c["deidentified_trace_ref"]
    masked = store.get_trace(ref)
    review = store.get_review_trace(ref, reader_role="reviewer", reader_user="u1", accessed_at="2026-10-15T00:00:00Z")
    assert review["frontend_message"]["utterance"] == UTT and review["extract_input"]["user_utterance"] == UTT
    assert UTT not in json.dumps(masked, ensure_ascii=False)                       # 기존 비식별 뷰에는 원문이 없다
    rm = copy.deepcopy(review)
    rm["frontend_message"]["utterance"] = rm["extract_input"]["user_utterance"] = "[MASKED]"
    assert rm == masked                                                           # 두 칸 말고는 같다: 다른 자유 문자열은 그대로 마스킹
    assert review["extract_output"]["note"] == "[MASKED]" and review["response_output"] == "[MASKED]"
    assert UTT not in json.dumps(c, ensure_ascii=False)                           # FailureCase(읽기 역할 전체가 읽는다)에도 원문이 없다


def test_keep_false_is_identical_to_before_and_review_read_falls_back_to_masked():
    _, off = run(cfg_with(False), clean_trace())
    _, unset = run(cfg_with(None), clean_trace())
    (a,), _ = off.list_cases()
    (b,), _ = unset.list_cases()
    assert a == b and off.list_aux("review_trace") == []
    got = off.get_review_trace(a["deidentified_trace_ref"], reader_role="reviewer", reader_user=None, accessed_at="2026-10-15T00:00:00Z")
    assert got == off.get_trace(a["deidentified_trace_ref"])
    assert off.list_aux("trace_access")[0]["original_kept"] is False


def test_policy_fingerprint_changes_only_when_kept():
    a = DeidPolicy.from_config(cfg_with(None)["deid"]).fingerprint()
    assert a == DeidPolicy.from_config(cfg_with(False)["deid"]).fingerprint()
    assert a != DeidPolicy.from_config(cfg_with(True)["deid"]).fingerprint()


def test_residual_pattern_in_the_kept_cell_fails_the_case():
    rep, store = run(cfg_with(True), pii_trace("s-1", "t-1"))                      # 원문 발화에 전화·이메일이 있다
    assert rep.deid_failed == 1 and rep.cases_published == 0 and store.list_cases()[0] == [] and store.list_aux("review_trace") == []
    ps = make_pseudonymizer(SALT)
    cfg = cfg_with(True)
    with pytest.raises(DeidError, match="잔존 패턴"):
        Deidentifier(DeidPolicy.from_config(cfg["deid"]), ps).deidentify({"envelope": envelope(pii_trace())})


def test_utterance_over_max_chars_keeps_only_the_front():
    cfg = cfg_with(True)
    cfg["deid"]["review_utterance"] = {"keep": True, "max_chars": 5}
    _, store = run(cfg, clean_trace())
    (c,), _ = store.list_cases()
    assert store.get_review_trace(c["deidentified_trace_ref"], reader_role="r", reader_user=None, accessed_at="2026-10-15T00:00:00Z")["frontend_message"]["utterance"] == UTT[:5]


@pytest.fixture
def served():
    _, store = run(cfg_with(True), clean_trace())
    (c,), _ = store.list_cases()
    s = make_server(LogStore.memory(), "ingest-tok", "127.0.0.1", 0, failures=store, read_tokens=parse_read_tokens("synthetic-data=read-tok"),
                    review_tokens=parse_read_tokens("reviewer=review-tok"), require_reviewer_identity=True)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}", c, store
    s.shutdown()


def call(base, path, token, user=None, method="GET", body=None):
    h = {"Authorization": f"Bearer {token}"}
    if user:
        h["X-Openit-User"] = user
    rq = urllib.request.Request(base + path, method=method, headers=h, data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(rq) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_only_the_review_token_reads_the_review_trace_and_every_read_is_recorded(served):
    base, c, store = served
    ref = c["deidentified_trace_ref"]
    assert call(base, f"/v1/review-traces/{ref}", "read-tok", "u1")[0] == 403                # 읽기 토큰
    assert call(base, f"/v1/review-traces/{ref}", "ingest-tok", "u1")[0] == 403
    assert call(base, f"/v1/review-traces/{ref}", "wrong")[0] == 401
    assert call(base, f"/v1/review-traces/{ref}", "review-tok")[0] == 401                    # 신원 헤더 없음
    assert store.list_aux("trace_access") == []                                              # 거절된 요청은 읽지 않았으므로 기록도 없다
    st, body = call(base, f"/v1/review-traces/{ref}", "review-tok", "kim.reviewer")
    assert st == 200 and body["frontend_message"]["utterance"] == UTT
    call(base, f"/v1/review-traces/{ref}", "review-tok", "kim.reviewer")
    rows = store.list_aux("trace_access")
    assert len(rows) == 2 and len({r["access_id"] for r in rows}) == 2                      # 읽을 때마다 한 행
    assert all(r["trace_ref"] == ref and r["reader_role"] == "reviewer" and r["reader_user"] == "kim.reviewer" and r["accessed_at"] and r["original_kept"] for r in rows)
    st, old = call(base, f"/v1/deidentified-traces/{ref}", "read-tok")                       # 기존 읽기 경로는 마스킹 그대로
    assert st == 200 and UTT not in json.dumps(old, ensure_ascii=False)
    assert call(base, f"/v1/review-traces/tr-nope", "review-tok", "u1")[0] == 404
    assert len(store.list_aux("trace_access")) == 2                                          # 없는 trace는 읽지 않았으므로 기록하지 않는다


def test_reviewed_input_with_the_original_utterance_is_rejected_but_a_rewrite_passes(served):
    base, c, store = served
    ok = {"question_id": "Q1", "current_question": "혈압약을 드시나요?", "user_utterance": "고혈압 약을 매일 아침 복용한다", "state": {}}
    st, e = call(base, f"/v1/cases/{c['case_id']}/review", "review-tok", "u1", "POST",
                 {"review_status": "CONFIRMED", "expected": {"action": "NEXT", "next_item": "END", "values": []}, "expected_kind": "LORA_A",
                  "reviewed_input": dict(ok, user_utterance=UTT)})
    assert st == 400 and "원문" in e["message"] and UTT not in e["message"]
    st, rec = call(base, f"/v1/cases/{c['case_id']}/review", "review-tok", "u1", "POST",
                   {"review_status": "CONFIRMED", "expected": {"action": "NEXT", "next_item": "END", "values": []}, "expected_kind": "LORA_A", "reviewed_input": ok})
    assert st == 201 and UTT not in json.dumps(rec, ensure_ascii=False)


def test_erasing_or_purging_a_case_removes_its_review_copy():
    _, store = run(cfg_with(True), clean_trace())
    (c,), _ = store.list_cases()
    assert len(store.list_aux("review_trace")) == 1
    store.purge_cases_created_before("2999-01-01T00:00:00Z", include_confirmed=True)
    assert store.list_aux("review_trace") == [] and store.list_cases()[0] == []
    _, store2 = run(cfg_with(True), clean_trace())
    (c2,), _ = store2.list_cases()
    store2.delete_by_trace_refs([c2["deidentified_trace_ref"]])
    assert store2.list_aux("review_trace") == []


def test_access_records_age_out_with_the_metrics_retention():
    _, store = run(cfg_with(True), clean_trace())
    (c,), _ = store.list_cases()
    ref = c["deidentified_trace_ref"]
    store.get_review_trace(ref, reader_role="reviewer", reader_user="u", accessed_at="2025-01-01T00:00:00Z")
    store.get_review_trace(ref, reader_role="reviewer", reader_user="u", accessed_at="2026-10-01T00:00:00Z")
    assert store.purge_aux_before("2026-01-01T00:00:00Z") == 1 and len(store.list_aux("trace_access")) == 1


def test_turning_keep_on_later_fills_the_copy_on_reprocess_without_changing_the_case():
    ps = make_pseudonymizer(SALT)
    log, store = LogStore.memory(clock=lambda: "2026-10-14T00:00:01Z"), FailureCaseStore.memory()
    log.send(envelope(clean_trace()))

    def go(cfg, **kw):
        return run_once(cfg=cfg, log=log, store=store, deid=Deidentifier(DeidPolicy.from_config(cfg["deid"]), ps), pseudonymize=ps,
                        detector=FailureDetector(cfg["detection"]["detectors"]), classifier=FailureClassifier(cfg["classification"]),
                        clock=lambda: "2026-10-15T00:00:00Z", evaluate_rollback=False, **kw)
    assert go(cfg_with(False)).cases_published == 1 and store.list_aux("review_trace") == []
    (before,), _ = store.list_cases()
    rep = go(cfg_with(True), reprocess=True)
    (after,), _ = store.list_cases()
    assert rep.cases_published == 0 and rep.skipped_diff == 0 and rep.cases_existing == 1 and before == after
    assert len(store.list_aux("review_trace")) == 1

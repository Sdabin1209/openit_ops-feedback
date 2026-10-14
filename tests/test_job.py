"""잡: 멱등성, 비식별 경계, 출처 릴리스, 설정 기반 분류."""
import json

from boundary_contracts.errors import BoundaryError

from conftest import FAKE_PII, envelope, make_trace, pii_trace
from ops_feedback.job import run_once
from ops_feedback.review import ReviewQueue


def run(p, **kw):
    return run_once(cfg=p["cfg"], log=p["log"], store=p["store"], deid=p["deid"], detectors=p["detectors"],
                    classifier=p["classifier"], clock=p["clock"], **kw)


def seed(p):
    p["log"].send(envelope(pii_trace("s-1", "t-1"), release_id="rel-0002"))     # 실패(폴백)
    p["log"].send(envelope(make_trace("s-1", "t-2"), release_id="rel-0002"))    # 정상


def all_cases(store):
    return store.list_cases(latest_only=False, limit=500)[0]


def test_job_creates_case_only_for_failures(pipeline):
    seed(pipeline)
    rep = run(pipeline)
    assert (rep.processed, rep.cases_published, rep.no_failure) == (2, 1, 1)
    (c,) = all_cases(pipeline["store"])
    assert c["source_release_id"] == "rel-0002" and c["deidentified"] is True
    assert c["failure_type"] == "T-FALLBACK" and c["review_status"] == "PENDING" and c["version"] == 1
    assert c["runtime_contract_version"] == 1
    assert c["ext"]["ops-feedback"]["deid_policy_id"] == "test-policy"


def test_no_raw_data_reaches_failure_store(pipeline):
    seed(pipeline)
    run(pipeline)
    st = pipeline["store"]
    (c,) = all_cases(st)
    blob = json.dumps(c, ensure_ascii=False) + json.dumps(st.get_trace(c["deidentified_trace_ref"]), ensure_ascii=False)
    for v in [*FAKE_PII.values(), "s-1", "t-1", "혈압"]:
        assert v not in blob


def test_idempotent_second_run_and_reprocess(pipeline):
    seed(pipeline)
    run(pipeline)
    before = json.dumps(all_cases(pipeline["store"]), sort_keys=True)
    rep2 = run(pipeline)
    assert rep2.processed == 0
    rep3 = run(pipeline, reprocess=True)                 # 처리 표시를 지우고 같은 Trace를 다시 처리
    assert rep3.processed == 2 and rep3.cases_published == 0 and rep3.cases_existing == 1
    assert json.dumps(all_cases(pipeline["store"]), sort_keys=True) == before


def test_duplicate_delivery_makes_one_case(pipeline):
    for _ in range(3):
        pipeline["log"].send(envelope(pii_trace()))
    run(pipeline)
    assert len(all_cases(pipeline["store"])) == 1


def test_deid_failure_creates_no_case(pipeline):
    pipeline["deid"].policy.keep_string_keys  # frozenset; 정책을 바꿔 발화를 남기게 한다
    from ops_feedback.deid import Deidentifier, DeidPolicy
    cfg = pipeline["cfg"]
    cfg["deid"]["keep_string_keys"] = cfg["deid"]["keep_string_keys"] + ["utterance"]
    pipeline["deid"] = Deidentifier(DeidPolicy.from_config(cfg["deid"]), "s", pipeline["log"].grant_deidentifier_view())
    pipeline["log"].send(envelope(pii_trace()))
    rep = run(pipeline)
    assert rep.deid_failed == 1 and rep.cases_published == 0 and all_cases(pipeline["store"]) == []
    assert run(pipeline).processed == 0                    # 실패로 표시되어 매번 다시 시도하지 않는다


def test_review_creates_new_version_and_is_idempotent(pipeline):
    seed(pipeline)
    run(pipeline)
    q = ReviewQueue(pipeline["store"], clock=lambda: "2026-10-16T00:00:00Z")
    (c,), _ = q.pending()
    r = q.review(c["case_id"], "CONFIRMED", "reviewer", expected={"items": []}, failure_type="X")
    assert r["version"] == 2 and r["review_status"] == "CONFIRMED" and r["expected"] == {"items": []}
    assert pipeline["store"].get_case(c["case_id"], version=1)["review_status"] == "PENDING"
    assert q.review(c["case_id"], "CONFIRMED", "reviewer", expected={"items": []}, failure_type="X")["version"] == 2
    assert q.pending()[0] == []


def test_rerun_after_review_does_not_touch_case(pipeline):
    seed(pipeline)
    run(pipeline)
    q = ReviewQueue(pipeline["store"])
    (c,), _ = q.pending()
    q.review(c["case_id"], "DISMISSED", "reviewer")
    run(pipeline, reprocess=True)
    assert pipeline["store"].get_case(c["case_id"])["version"] == 2


def test_raw_purge_only_when_configured(pipeline):
    seed(pipeline)
    pipeline["cfg"]["retention"]["raw_days"] = 0
    pipeline["clock"] = lambda: "2030-01-01T00:00:00Z"
    rep = run(pipeline)
    assert rep.purged_raw == 2
    assert len(all_cases(pipeline["store"])) == 1          # 사례는 원본 삭제와 무관

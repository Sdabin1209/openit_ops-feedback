"""FailureCase.ext["ops-feedback"]를 쓰는 곳(job.py의 사례 생성, review.py의 검토 게시)의 출력이 계약 OpsFeedbackFailureCaseExt를 지킨다.
계약 설명의 '쓰는 쪽도 아래 키 밖을 쓰지 않는다'는 제안도 시험으로 닫는다. 실행 코드에 자기 검사를 넣지 않고(R7) 시험이 본다(U47 6)."""
import pytest
from boundary_contracts import schema_dir, validator
import json

from conftest import envelope, make_trace, pii_trace
from ops_feedback.components.deidentifier import DeidPolicy
from tdeid import Deidentifier
from ops_feedback.wiring.pipeline import run_once
from ops_feedback.wiring.review_flow import ReviewFlow as ReviewQueue
from test_pause_reason import SIG, orch

SCHEMA = json.loads((schema_dir() / "OpsFeedbackFailureCaseExt.schema.json").read_text(encoding="utf-8"))
ALLOWED = set(SCHEMA["properties"])          # 이 영역이 쓰는 키는 계약이 이름 붙인 것뿐이다


def check(ext: dict) -> None:
    mine = ext["ops-feedback"]
    errs = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message[:100]}" for e in validator("OpsFeedbackFailureCaseExt").iter_errors(mine)]
    assert not errs, errs
    assert set(mine) <= ALLOWED, f"계약이 이름 붙이지 않은 키를 쓴다: {sorted(set(mine) - ALLOWED)}"


def run(p, **kw):
    return run_once(cfg=p["cfg"], log=p["log"], store=p["store"], deid=p["deid"], detector=p["detector"], pseudonymize=p["pseudonymize"],
                    classifier=p["classifier"], clock=p["clock"], evaluate_rollback=False, **kw)


def test_job_written_ext_follows_the_contract(pipeline):
    pipeline["log"].send(envelope(pii_trace("s-1", "t-1"), release_id="rel-0002"))
    run(pipeline)
    (c,), _ = pipeline["store"].list_cases()
    check(c["ext"])


def test_job_written_ext_with_pause_reason_follows_the_contract(pipeline):
    pipeline["deid"] = Deidentifier(DeidPolicy.from_config({**pipeline["cfg"]["deid"], "keep_pause_reason": True}), "test-salt",
                                    pipeline["log"].grant_deidentifier_view())
    for i, r in enumerate(["RISK", "REASK_LIMIT", "OTHER"]):
        pipeline["log"].send(envelope(make_trace("s", f"t{i}", orchestrator_output=orch(dict(SIG, pause_reason=r))), release_id="rel-0002"))
    run(pipeline)
    items, _ = pipeline["store"].list_cases(limit=50)
    assert len(items) == 3
    for c in items:
        check(c["ext"])
        assert c["ext"]["ops-feedback"]["pause_reason"] in ("RISK", "REASK_LIMIT", "OTHER")


@pytest.mark.parametrize("note", [None, "확인함"])
def test_review_written_ext_follows_the_contract(pipeline, note):
    pipeline["log"].send(envelope(pii_trace("s-1", "t-1"), release_id="rel-0002"))
    run(pipeline)
    (c,), _ = pipeline["store"].list_cases()
    q = ReviewQueue(pipeline["store"])
    for status in ("DISMISSED", "PENDING"):
        rec = q.review(c["case_id"], status, "reviewer", note=note)
        check(rec["ext"])
        assert rec["ext"]["ops-feedback"]["review"]["reviewer_role"] == "reviewer"
    rec = q.review(c["case_id"], "DISMISSED", "reviewer", reviewed_by="kim.reviewer", note=note)
    check(rec["ext"])
    assert rec["ext"]["ops-feedback"]["reviewed_by"] == "kim.reviewer"

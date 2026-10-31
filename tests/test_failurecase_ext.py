"""FailureCase.ext["ops-feedback"]를 쓰는 곳(job.py의 사례 생성, review.py의 검토 게시)의 출력이 계약 OpsFeedbackFailureCaseExt를 지킨다.
계약 설명의 '쓰는 쪽도 아래 키 밖을 쓰지 않는다'는 제안도 시험으로 닫는다. 생산자는 내보내기 전에 실행 코드에서도 계약으로 검증한다(R7, U426): 아래 마지막 시험들이 위반 출력이 나가지 않음을 확인한다."""
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


# ---- U426: 생산자 검증(실행 중에도 계약으로 본다) ----

def test_case_with_an_ext_the_contract_rejects_is_not_built():
    """탐지기가 계약 패턴(^[A-Z][A-Z0-9_]*$) 밖의 rule_id를 내면 사례를 만들지 않는다."""
    from boundary_contracts.errors import BoundaryError
    from ops_feedback.components import case_builder
    import copy
    ok = json.loads((__import__("pathlib").Path(__file__).resolve().parent.parent / "schemas" / "examples" / "CaseBuildRequest.valid.json").read_text("utf-8"))
    case_builder.build(ok)
    bad = copy.deepcopy(ok)
    bad["detections"][0]["rule_id"] = "lower-case"
    bad["classification"]["primary_rule"] = "lower-case"
    with pytest.raises(BoundaryError) as e:
        case_builder.build(bad)
    assert e.value.code == "UNAVAILABLE"


def test_job_publishes_nothing_and_reports_when_a_detector_breaks_the_ext_contract(pipeline):
    from ops_feedback.components.failure_detector import FailureDetector, make_detection

    class Bad:
        name = "bad"

        def detect(self, trace):
            return [make_detection("lower-case", {})]
    det = FailureDetector([])
    det._detectors = [Bad()]
    pipeline["log"].send(envelope(pii_trace("s-1", "t-1"), release_id="rel-0002"))
    pipeline["detector"] = det
    rep = run(pipeline)
    assert pipeline["store"].list_cases()[0] == [] and rep.errors and rep.errors[0]["error"] == "UNAVAILABLE"


def test_review_rejects_a_malformed_reviewer_id_as_bad_input_not_as_a_server_fault(pipeline):
    """검토자 이름은 요청에서 온 값이다. Id 형식이 아니면 입구에서 INVALID_RECORD(400, 재시도 불가)로 거부하고 사례·게시는 건드리지 않는다."""
    from boundary_contracts.errors import BoundaryError
    pipeline["log"].send(envelope(pii_trace("s-1", "t-1"), release_id="rel-0002"))
    run(pipeline)
    (c,), _ = pipeline["store"].list_cases()
    q = ReviewQueue(pipeline["store"])
    with pytest.raises(BoundaryError) as e:
        q.review(c["case_id"], "DISMISSED", "reviewer", reviewed_by="not an id")
    assert e.value.code == "INVALID_RECORD" and not e.value.retryable
    assert pipeline["store"].get_case(c["case_id"])["version"] == 1


def test_review_does_not_publish_an_ext_the_contract_rejects_after_input_passed(pipeline):
    """입력 검사를 통과했는데도 조립한 ext가 계약을 어기면(여기서는 설정의 토큰 역할 이름이 Id 형식이 아니라 reviewed_by로 쓰일 때) 서버 결함이라 UNAVAILABLE이고 게시하지 않는다."""
    from boundary_contracts.errors import BoundaryError
    pipeline["log"].send(envelope(pii_trace("s-1", "t-1"), release_id="rel-0002"))
    run(pipeline)
    (c,), _ = pipeline["store"].list_cases()
    q = ReviewQueue(pipeline["store"])
    with pytest.raises(BoundaryError) as e:
        q.review(c["case_id"], "DISMISSED", "bad role name")      # reviewed_by 없이 역할 이름이 대신 쓰인다
    assert e.value.code == "UNAVAILABLE"
    assert pipeline["store"].get_case(c["case_id"])["version"] == 1

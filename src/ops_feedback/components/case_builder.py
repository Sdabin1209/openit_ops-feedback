"""실패 사례 조립. 입력 CaseBuildRequest(비식별 결과+분류+탐지), 출력 CaseBuildResult(FailureCase version 1 + 저장할 trace와 참조).

모든 값이 입력에서 정해져 같은 입력이면 같은 사례가 나온다(멱등: case_id, trace ref, created_at이 모두 입력에서 결정된다).
case_id는 세션·턴의 의사 식별자에서 만든다(원래 ID는 이 컴포넌트에 오지 않는다).
"""
from __future__ import annotations

from boundary_contracts import PIPELINE_SCHEMA_VERSION  # 새 레코드는 계약 상수의 버전으로 쓴다(값을 코드에 적지 않는다)
from boundary_contracts.canonical import sha256_hash
from boundary_contracts.errors import BoundaryError

from ..types.CaseBuildRequest import CaseBuildRequest
from ..types.CaseBuildResult import CaseBuildResult
from ..wiring.validate import check


def _check_ext(mine: dict) -> None:
    """생산자 검증(R7): 쓰는 ext["ops-feedback"]가 계약 OpsFeedbackFailureCaseExt를 지키는지 내보내기 전에 본다. 어기면 내보내지 않는다(코드·탐지기 결함이지 입력 잘못이 아니다)."""
    try:
        check("OpsFeedbackFailureCaseExt", mine, contract=True)
    except BoundaryError:
        raise BoundaryError("UNAVAILABLE", "사례 ext가 OpsFeedbackFailureCaseExt를 어긴다(서버 결함)") from None


def build(request: CaseBuildRequest) -> CaseBuildResult:
    check("CaseBuildRequest", request)
    deid, cls, dets = request["deid"], request["classification"], request["detections"]
    trace_ref = "tr-" + sha256_hash(deid["trace"])[7:39]
    mine = {
        "detections": [{"rule_id": d["rule_id"], "detail": d["detail"]} for d in sorted(dets, key=lambda d: d["rule_id"])],
        "primary_rule": cls["primary_rule"],
        "deid_policy_id": request["policy_id"],
        "deid_stats": deid["stats"],
    }
    if "pause_reason" in request:
        mine["pause_reason"] = request["pause_reason"]          # 식별정보가 아닌 열거값(ADR 0014)
    case = {
        "pipeline_schema_version": PIPELINE_SCHEMA_VERSION,
        "case_id": "fc-" + sha256_hash({"s": request["session_pseudonym"], "t": request["turn_pseudonym"]})[7:39],
        "version": 1,
        "created_at": request["received_at"],
        "source_release_id": request["release_id"],
        "runtime_contract_version": request["runtime_contract_version"],
        "deidentified": True,
        "deidentified_trace_ref": trace_ref,
        "failure_type": cls["failure_type"],
        "detection_method": sorted({d["method"] for d in dets})[0],
        "review_status": "PENDING",
        "ext": {"ops-feedback": mine},
    }
    if "question_set" in request:
        case["question_set"] = request["question_set"]
    _check_ext(mine)
    out: CaseBuildResult = {"case": case, "trace_ref": trace_ref, "trace": deid["trace"]}
    check("CaseBuildResult", out)
    return out

"""롤백 요청 조립. 입력 RollbackPlanRequest, 출력 RollbackPlan(계약 RollbackRequest + 근거 지표 스냅샷). 요청만 만들며 활성 포인터는 게이트만 바꾼다.

request_id는 (현재, 되돌릴, 포인터 버전)에서만 정해지므로 같은 상황의 재실행·동시 실행은 같은 요청을 가리킨다(멱등).
"""
from __future__ import annotations

from boundary_contracts import PIPELINE_SCHEMA_VERSION  # 새 레코드는 계약 상수의 버전으로 쓴다(값을 코드에 적지 않는다)
from boundary_contracts.canonical import sha256_hash

from ..types.RollbackPlan import RollbackPlan
from ..types.RollbackPlanRequest import RollbackPlanRequest
from ..wiring.validate import check


def build_plan(request: RollbackPlanRequest) -> RollbackPlan:
    check("RollbackPlanRequest", request)
    a, p, now = request["assessment"], request["policy"], request["now"]
    cur, prev = request["from_release_id"], request["to_release_id"]
    request_id = "rb-" + sha256_hash({"from": cur, "to": prev, "pointer_version": request["pointer_version"]})[7:31]
    metric = {"release_id": cur, "window_start": a["window_start"], "window_end": now, "total_turns": a["total_turns"],
              "failure_cases": a["failures"], "failure_rate": a["rate"], "by_failure_type": a["by_failure_type"],
              "by_pause_reason": a["by_pause_reason"], "counted_review_statuses": sorted(p["counted_review_statuses"])}
    metric_id = "metric-" + sha256_hash(metric)[7:31]
    req = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "request_id": request_id, "requested_at": now,
           "from_release_id": cur, "to_release_id": prev,
           "reason": f"실패율 {a['rate']:.4f}이 기준 {p['max_failure_rate']}을 넘었다(최근 {p['window_hours']}시간)",
           "evidence_refs": [metric_id], "requested_by_role": p["requested_by_role"]}
    out: RollbackPlan = {"request": req, "metric": dict(metric, metric_id=metric_id)}
    check("RollbackPlan", out)
    return out

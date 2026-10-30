"""같은 case_id의 version 1이 이미 있을 때 어떻게 할지 정한다. 입력 ReconcileRequest, 출력 ReconcileDecision."""
from __future__ import annotations

from boundary_contracts.canonical import sha256_hash

from ..types.ReconcileDecision import ReconcileDecision
from ..types.ReconcileRequest import ReconcileRequest
from ..wiring.validate import check


def _core(c: dict) -> str:
    return sha256_hash({k: c[k] for k in c if k != "ext"} | {"ext": c.get("ext")})


def reconcile(request: ReconcileRequest) -> ReconcileDecision:
    check("ReconcileRequest", request)
    existing, cand = request.get("existing"), request["candidate"]
    if existing is None:
        action = "PUBLISH"
    elif existing["version"] == 1 and _core(existing) == _core(cand):
        action = "EXISTING"
    elif existing["version"] == 1 and existing["review_status"] == "PENDING":
        action = "SKIPPED_DIFF"       # 설정이 바뀌어 결과가 달라졌다. 기존 사례를 건드리지 않는다.
    else:
        action = "EXISTING"           # 검토가 끝났거나 새 version이 있다
    out: ReconcileDecision = {"action": action}
    return out

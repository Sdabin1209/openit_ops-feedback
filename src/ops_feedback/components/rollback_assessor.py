"""롤백 판단 1단계: 현재 릴리스의 실패율이 기준을 넘었는가. 입력 RollbackAssessRequest, 출력 RollbackAssessment.

기준(window_hours, min_turns, max_failure_rate, 집계할 review_status)은 사용자가 정하므로 입력(설정)으로만 받는다. 사례 요약은 창 밖의 것도 올 수 있어 여기서 창과 상태로 거른다.
"""
from __future__ import annotations

from datetime import timedelta

from ..types.RollbackAssessRequest import RollbackAssessRequest
from ..types.RollbackAssessment import RollbackAssessment
from ..wiring.clock import fmt_ts, parse_ts
from ..wiring.validate import check


def assess(request: RollbackAssessRequest) -> RollbackAssessment:
    check("RollbackAssessRequest", request)
    p = request["policy"]
    since = fmt_ts(parse_ts(request["now"]) - timedelta(hours=p["window_hours"]))
    counted = set(p["counted_review_statuses"])
    by_type: dict[str, int] = {}
    by_pause: dict[str, int] = {}
    for c in request["cases"]:
        if c["created_at"] >= since and c["review_status"] in counted:
            by_type[c["failure_type"]] = by_type.get(c["failure_type"], 0) + 1
            if "pause_reason" in c:      # 사유가 기록된 사례만 센다(없는 것을 추정해 채우지 않는다)
                by_pause[c["pause_reason"]] = by_pause.get(c["pause_reason"], 0) + 1
    total, failures = request["total_turns"], sum(by_type.values())
    out: RollbackAssessment = {"verdict": "INSUFFICIENT", "window_start": since, "total_turns": total, "failures": failures,
                               "by_failure_type": dict(sorted(by_type.items())), "by_pause_reason": dict(sorted(by_pause.items()))}
    if total >= p["min_turns"]:
        out["rate"] = failures / total
        out["verdict"] = "BELOW" if out["rate"] <= p["max_failure_rate"] else "EXCEEDED"
    check("RollbackAssessment", out)
    return out

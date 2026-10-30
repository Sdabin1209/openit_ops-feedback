"""관리 도구 purge가 지울 기준 시각 계산. 입력 PurgeRequest, 출력 PurgeCutoffs. 기간은 사용자가 정하며 기본값이 없다(주지 않은 것은 지우지 않는다)."""
from __future__ import annotations

from datetime import timedelta

from ..types.PurgeCutoffs import PurgeCutoffs
from ..types.PurgeRequest import PurgeRequest
from ..wiring.clock import fmt_ts, parse_ts
from ..wiring.validate import check


def plan(request: PurgeRequest) -> PurgeCutoffs:
    check("PurgeRequest", request)
    now = parse_ts(request["now"])

    def cut(key: str):
        return fmt_ts(now - timedelta(days=request[key])) if key in request else None
    out: PurgeCutoffs = {"raw_cutoff": cut("raw_days"), "cases_cutoff": cut("cases_days"), "metrics_cutoff": cut("metrics_days"),
                         "include_confirmed": request["include_confirmed"], "include_unprocessed": request["include_unprocessed"]}
    return out

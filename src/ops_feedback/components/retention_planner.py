"""보존 기한 계산. 입력 RetentionRequest(지금 시각+설정 retention), 출력 RetentionCutoffs(지울 기준 시각, null이면 지우지 않는다)."""
from __future__ import annotations

from datetime import timedelta

from ..types.RetentionCutoffs import RetentionCutoffs
from ..types.RetentionRequest import RetentionRequest
from ..wiring.clock import fmt_ts, parse_ts
from ..wiring.validate import check

FOREVER = "forever"
# 출처: D88/U4 최소 보존 잠정 기본값(원본 30일, 사례 180일, 기록 1년). docs/adr/0007-retention-defaults.md
# failed_raw_days(재시도 대상 DEID_FAILED·PUBLISH_FAILED 원본의 기한)는 기본값이 없다: 적지 않으면 raw_days를 따른다(raw_days가 forever면 forever).
# 근거 없는 별도 숫자를 채우면 운영자가 정한 raw_days보다 먼저(또는 forever인데도) 재처리 대상이 지워진다(점검 round3 M1). docs/adr/0007.
RETENTION_DEFAULTS = {"raw_days": 30, "cases_days": 180, "metrics_days": 365}


def plan(request: RetentionRequest) -> RetentionCutoffs:
    check("RetentionRequest", request)
    ret, now = request["retention"], parse_ts(request["now"])

    def cutoff_of(key: str):
        d = ret[key] if key in ret else RETENTION_DEFAULTS[key]
        return None if d == FOREVER else fmt_ts(now - timedelta(days=d))

    raw = cutoff_of("raw_days")
    out: RetentionCutoffs = {
        "raw_cutoff": raw,
        # 재시도 대상 원본: failed_raw_days를 명시했을 때만 별도 기한, 아니면 raw_days를 그대로 따른다
        "failed_raw_cutoff": cutoff_of("failed_raw_days") if "failed_raw_days" in ret else raw,
        "cases_cutoff": cutoff_of("cases_days"), "metrics_cutoff": cutoff_of("metrics_days"),
        "purge_unprocessed": ret.get("purge_unprocessed", False), "cases_purge_confirmed": ret.get("cases_purge_confirmed", True),
        "minimize_raw": ret.get("raw_after_processing", "keep") == "minimize",
    }
    check("RetentionCutoffs", out)
    return out

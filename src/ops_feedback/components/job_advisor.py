"""운영자에게 알릴 설정 상태(경고, 오류 아님). 입력 AdvisorRequest, 출력 Advisories. 종료 코드에 영향 없다."""
from __future__ import annotations

from ..types.AdvisorRequest import AdvisorRequest
from ..types.Advisories import Advisories
from ..wiring.validate import check

FOREVER = "forever"
DEFAULT_DAYS = {"raw_days": 30, "cases_days": 180, "metrics_days": 365}      # 출처: ADR 0007(보존 기본값). retention_planner와 같은 값을 알려 주기만 한다


def advise(request: AdvisorRequest) -> Advisories:
    check("AdvisorRequest", request)
    ret, out = request["retention"], []
    for key, default in DEFAULT_DAYS.items():
        if ret.get(key, default) == FOREVER:
            out.append(f"RETENTION_FOREVER: retention.{key}가 forever라 무기한 보관된다(U4 기본은 {default}일)")
    if ret.get("failed_raw_days") == FOREVER:       # 명시했을 때만. 적지 않으면 raw_days를 따르므로 위의 raw_days 경고가 그 경우를 덮는다
        out.append("RETENTION_FOREVER: retention.failed_raw_days가 forever라 재시도 대상 원본이 무기한 보관된다")
    keys = request.get("unvalidated_keep_keys") or []
    if keys:
        out.append("DEID_KEEP_KEYS_UNVALIDATED: 값 검증 패턴이 없는 keep_string_keys: " + ",".join(sorted(keys)))
    oldest, cut = request.get("oldest_unprocessed_received_at"), request.get("raw_cutoff")
    if oldest and cut and oldest < cut and not request.get("purge_unprocessed", False):
        out.append("UNPROCESSED_RAW_PAST_RETENTION: 보관 기한을 넘긴 원본이 아직 처리되지 않았다(지우지 않았다). 잡이 멈췄거나 처리가 막혔는지 확인한다")
    res: Advisories = {"warnings": out}
    return res

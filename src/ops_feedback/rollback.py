"""롤백 요청 생성. 요청만 하며 활성 포인터는 게이트만 바꾼다.

기준(window_hours, min_turns, max_failure_rate, 집계할 review_status)은 사용자가 정하므로 설정으로만 받는다.
릴리스 정보는 I4 ReleaseReader(boundary_contracts.sdk.ReleaseReader)로만 읽고, 요청 전달은 I5 GateService로 한다.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from boundary_contracts.canonical import sha256_hash
from boundary_contracts.errors import BoundaryError
from boundary_contracts.sdk import GateService, ReleaseReader

from .clock import Clock, fmt_ts, parse_ts, utc_now
from .failurestore import FailureStoreImpl
from .logstore import LogStore
from .schemas import validate_record

from boundary_contracts import PIPELINE_SCHEMA_VERSION  # 새 레코드는 계약 상수의 버전으로 쓴다(값을 코드에 적지 않는다)


class RollbackPlanner:
    def __init__(self, *, store: FailureStoreImpl, log: LogStore, release_reader: ReleaseReader,
                 gate: Optional[GateService], policy: dict, clock: Clock = utc_now) -> None:
        if not isinstance(policy.get("auto_submit"), bool):      # 문자열 "false"는 참으로 평가되어 실수로 자동 전송된다
            raise ValueError("rollback.auto_submit은 true 또는 false(불리언)여야 한다")
        self._store, self._log, self._rr, self._gate, self._p, self._clock = store, log, release_reader, gate, policy, clock

    def _previous_release(self, current: str) -> Optional[str]:
        cursor = None
        seen_current = False
        while True:
            items, cursor = self._rr.list_releases(cursor=cursor, limit=100)     # 최신부터
            for m in items:
                if seen_current:
                    return m["release_id"]
                if m["release_id"] == current:
                    seen_current = True
            if cursor is None:
                return None

    def _failure_counts(self, release_id: str, since: str, by_pause: Optional[dict] = None) -> dict[str, int]:
        counted = set(self._p["counted_review_statuses"])
        by_type: dict[str, int] = {}
        cursor = None
        while True:
            items, cursor = self._store.list_cases(source_release_id=release_id, cursor=cursor, limit=500)
            for c in items:
                if c["created_at"] >= since and c["review_status"] in counted:
                    by_type[c["failure_type"]] = by_type.get(c["failure_type"], 0) + 1
                    pr = ((c.get("ext") or {}).get("ops-feedback") or {}).get("pause_reason")
                    if by_pause is not None and pr is not None:      # 사유가 기록된 사례만 센다(없는 것을 추정해 채우지 않는다)
                        by_pause[pr] = by_pause.get(pr, 0) + 1
            if cursor is None:
                return by_type

    def evaluate(self) -> dict:
        """현재 활성 릴리스의 실패율을 보고 필요하면 RollbackRequest를 만든다(설정이 켜져 있으면 게이트에 전달)."""
        out: dict = {"action": "NONE"}
        ptr = self._rr.get_active_pointer()
        if ptr is None:
            out["reason"] = "활성 릴리스 없음"
            return out
        cur = ptr["release_id"]
        now = self._clock()
        since = fmt_ts(parse_ts(now) - timedelta(hours=self._p["window_hours"]))
        total = self._log.counts_by_release(since).get(cur, 0)
        by_pause: dict[str, int] = {}
        by_type = self._failure_counts(cur, since, by_pause)
        failures = sum(by_type.values())
        out.update(release_id=cur, total_turns=total, failures=failures, by_pause_reason=dict(sorted(by_pause.items())))
        if total < self._p["min_turns"]:
            out["reason"] = "표본 부족"
            return out
        rate = failures / total
        out["rate"] = rate
        if rate <= self._p["max_failure_rate"]:
            out["reason"] = "기준 이하"
            return out
        prev = self._previous_release(cur)
        if prev is None:
            out["reason"] = "되돌릴 릴리스 없음"
            return out
        request_id = "rb-" + sha256_hash({"from": cur, "to": prev, "pointer_version": ptr["pointer_version"]})[7:31]
        stored = self._store.get_aux("rollback_request", request_id)
        if stored is None:
            # 지표 스냅샷은 요청을 처음 만들 때만 쓴다(실행마다 window_end가 달라 매번 새 행이 쌓이는 것을 막는다).
            # 요청 id는 (현재, 되돌릴, 포인터 버전)에서만 정해지므로 같은 상황의 재실행·동시 실행은 같은 요청을 가리킨다.
            metric = {"release_id": cur, "window_start": since, "window_end": now, "total_turns": total,
                      "failure_cases": failures, "failure_rate": rate, "by_failure_type": dict(sorted(by_type.items())),
                      "by_pause_reason": dict(sorted(by_pause.items())),
                      "counted_review_statuses": sorted(self._p["counted_review_statuses"])}
            metric_id = "metric-" + sha256_hash(metric)[7:31]
            self._store.put_aux("metric", metric_id, dict(metric, metric_id=metric_id))
            req = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "request_id": request_id, "requested_at": now,
                   "from_release_id": cur, "to_release_id": prev,
                   "reason": f"실패율 {rate:.4f}이 기준 {self._p['max_failure_rate']}을 넘었다(최근 {self._p['window_hours']}시간)",
                   "evidence_refs": [metric_id], "requested_by_role": self._p["requested_by_role"]}
            validate_record("RollbackRequest", req)
            self._store.put_aux("rollback_request", request_id, req)       # 동시에 만든 다른 실행이 먼저 쓰면 그쪽이 남는다
            stored = self._store.get_aux("rollback_request", request_id) or req
        out.update(action="REQUEST_CREATED", request_id=request_id, to_release_id=prev)
        if self._p["auto_submit"] and self._gate is not None and self._store.get_aux("rollback_receipt", request_id) is None:
            try:
                receipt = self._gate.request_rollback(stored)
                validate_record("RollbackReceipt", receipt)
                if receipt.get("request_id") != request_id:       # 다른 요청의 영수증을 이 요청의 것으로 저장하지 않는다(원칙 10)
                    raise BoundaryError("INTEGRITY_ERROR", "영수증의 request_id가 요청과 다르다")
                self._store.put_aux("rollback_receipt", request_id, receipt)
                out["receipt_status"] = receipt["status"]
            except BoundaryError as e:
                out["submit_error"] = e.code           # UNAVAILABLE이면 다음 실행에서 같은 request_id로 다시 보낸다
        return out

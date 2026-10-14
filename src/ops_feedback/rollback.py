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

PIPELINE_SCHEMA_VERSION = "1.0"


class RollbackPlanner:
    def __init__(self, *, store: FailureStoreImpl, log: LogStore, release_reader: ReleaseReader,
                 gate: Optional[GateService], policy: dict, clock: Clock = utc_now) -> None:
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

    def _failure_counts(self, release_id: str, since: str) -> dict[str, int]:
        counted = set(self._p["counted_review_statuses"])
        by_type: dict[str, int] = {}
        cursor = None
        while True:
            items, cursor = self._store.list_cases(source_release_id=release_id, cursor=cursor, limit=500)
            for c in items:
                if c["created_at"] >= since and c["review_status"] in counted:
                    by_type[c["failure_type"]] = by_type.get(c["failure_type"], 0) + 1
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
        by_type = self._failure_counts(cur, since)
        failures = sum(by_type.values())
        out.update(release_id=cur, total_turns=total, failures=failures)
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
        metric = {"release_id": cur, "window_start": since, "window_end": now, "total_turns": total,
                  "failure_cases": failures, "failure_rate": rate, "by_failure_type": dict(sorted(by_type.items())),
                  "counted_review_statuses": sorted(self._p["counted_review_statuses"])}
        metric_id = "metric-" + sha256_hash(metric)[7:31]
        self._store.put_aux("metric", metric_id, dict(metric, metric_id=metric_id))
        request_id = "rb-" + sha256_hash({"from": cur, "to": prev, "pointer_version": ptr["pointer_version"]})[7:31]
        stored = self._store.get_aux("rollback_request", request_id)
        if stored is None:
            req = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "request_id": request_id, "requested_at": now,
                   "from_release_id": cur, "to_release_id": prev,
                   "reason": f"실패율 {rate:.4f}이 기준 {self._p['max_failure_rate']}을 넘었다(최근 {self._p['window_hours']}시간)",
                   "evidence_refs": [metric_id], "requested_by_role": self._p["requested_by_role"]}
            validate_record("RollbackRequest", req)
            self._store.put_aux("rollback_request", request_id, req)
            stored = req
        out.update(action="REQUEST_CREATED", request_id=request_id, to_release_id=prev)
        if self._p["auto_submit"] and self._gate is not None and self._store.get_aux("rollback_receipt", request_id) is None:
            try:
                receipt = self._gate.request_rollback(stored)
                validate_record("RollbackReceipt", receipt)
                self._store.put_aux("rollback_receipt", request_id, receipt)
                out["receipt_status"] = receipt["status"]
            except BoundaryError as e:
                out["submit_error"] = e.code           # UNAVAILABLE이면 다음 실행에서 같은 request_id로 다시 보낸다
        return out

"""롤백 평가의 조립: 이웃 읽기(link_release_read) -> 실패율 판단(rollback_assessor) -> 되돌릴 릴리스(release_predecessor) -> 요청 조립(rollback_request_builder)
-> 저장 -> 전달(link_gate_send). 요청만 하며 활성 포인터는 게이트만 바꾼다. 기준은 사용자가 정하므로 설정으로만 받는다."""
from __future__ import annotations

from typing import Optional

from boundary_contracts.errors import BoundaryError

from ..components import release_predecessor, rollback_assessor, rollback_request_builder
from ..components.failure_case_store import FailureCaseStore
from ..components.link_gate_send import GateLink
from ..components.link_release_read import ReleaseLink
from .clock import Clock, utc_now
from .log_store import LogStore
from .validate import check_part


class RollbackPlanner:
    def __init__(self, *, store: FailureCaseStore, log: LogStore, release_reader, gate, policy: dict, clock: Clock = utc_now) -> None:
        # auto_submit이 불리언인지(문자열 "false"가 참으로 평가되어 실수로 자동 전송되는 것을 막는다) 등은 계약 OpsFeedbackJobConfig.rollback이 정한다
        check_part("OpsFeedbackJobConfig", "/properties/rollback", policy, "롤백 설정")
        self._store, self._log, self._p, self._clock = store, log, policy, clock
        self._rr = ReleaseLink(release_reader)
        self._gate = GateLink(gate) if gate is not None else None

    def _case_summaries(self, release_id: str, since: str) -> list:
        out, cursor = [], None
        while True:
            items, cursor = self._store.list_cases(source_release_id=release_id, cursor=cursor, limit=500)
            for c in items:
                if c["created_at"] >= since:
                    s = {"failure_type": c["failure_type"], "review_status": c["review_status"], "created_at": c["created_at"]}
                    pr = ((c.get("ext") or {}).get("ops-feedback") or {}).get("pause_reason")
                    if pr is not None:
                        s["pause_reason"] = pr
                    out.append(s)
            if cursor is None:
                return out

    def _previous_release(self, current: str) -> Optional[str]:
        seen: list = []
        for page in self._rr.release_id_pages():     # 최신부터. 찾으면 더 읽지 않는다
            seen += page
            r = release_predecessor.find_predecessor({"current": current, "release_ids": seen})
            if r["found"]:
                return r["previous"]
        return None

    def evaluate(self) -> dict:
        """현재 활성 릴리스의 실패율을 보고 필요하면 RollbackRequest를 만든다(설정이 켜져 있으면 게이트에 전달)."""
        out: dict = {"action": "NONE"}
        ptr = self._rr.active_pointer()
        if ptr is None:
            out["reason"] = "활성 릴리스 없음"
            return out
        cur = ptr["release_id"]
        now = self._clock()
        # 창 시작은 판단 컴포넌트가 정한다: 먼저 같은 입력으로 창 시작 시각만 얻는다(사례·턴 수는 그 시각 이후만 읽는다)
        probe = rollback_assessor.assess({"now": now, "release_id": cur, "total_turns": 0, "cases": [], "policy": self._p})
        since = probe["window_start"]
        total = self._log.counts_by_release(since).get(cur, 0)
        a = rollback_assessor.assess({"now": now, "release_id": cur, "total_turns": total, "cases": self._case_summaries(cur, since), "policy": self._p})
        out.update(release_id=cur, total_turns=total, failures=a["failures"], by_pause_reason=a["by_pause_reason"])
        if a["verdict"] == "INSUFFICIENT":
            out["reason"] = "표본 부족"
            return out
        out["rate"] = a["rate"]
        if a["verdict"] == "BELOW":
            out["reason"] = "기준 이하"
            return out
        prev = self._previous_release(cur)
        if prev is None:
            out["reason"] = "되돌릴 릴리스 없음"
            return out
        plan = rollback_request_builder.build_plan({"now": now, "from_release_id": cur, "to_release_id": prev, "pointer_version": ptr["pointer_version"],
                                                    "assessment": a, "policy": self._p})
        request_id = plan["request"]["request_id"]
        stored = self._store.get_aux("rollback_request", request_id)
        if stored is None:
            # 지표 스냅샷은 요청을 처음 만들 때만 쓴다(실행마다 window_end가 달라 매번 새 행이 쌓이는 것을 막는다).
            # 요청 id는 (현재, 되돌릴, 포인터 버전)에서만 정해지므로 같은 상황의 재실행·동시 실행은 같은 요청을 가리킨다.
            self._store.put_aux("metric", plan["metric"]["metric_id"], plan["metric"])
            self._store.put_aux("rollback_request", request_id, plan["request"])       # 동시에 만든 다른 실행이 먼저 쓰면 그쪽이 남는다
            stored = self._store.get_aux("rollback_request", request_id) or plan["request"]
        out.update(action="REQUEST_CREATED", request_id=request_id, to_release_id=prev)
        if self._p["auto_submit"] and self._gate is not None and self._store.get_aux("rollback_receipt", request_id) is None:
            try:
                receipt = self._gate.request_rollback(stored)
                self._store.put_aux("rollback_receipt", request_id, receipt)
                out["receipt_status"] = receipt["status"]
            except BoundaryError as e:
                out["submit_error"] = e.code           # UNAVAILABLE이면 다음 실행에서 같은 request_id로 다시 보낸다
        return out

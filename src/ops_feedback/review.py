"""사람 검토 큐. 검토 결과는 version을 올린 새 FailureCase로 게시한다(레코드는 불변, D25)."""
from __future__ import annotations

from typing import Optional

from boundary_contracts.errors import BoundaryError

from .clock import Clock, utc_now
from .failurestore import FailureStoreImpl


class ReviewQueue:
    def __init__(self, store: FailureStoreImpl, *, clock: Clock = utc_now) -> None:
        self._store = store
        self._clock = clock

    def pending(self, *, failure_type: Optional[str] = None, source_release_id: Optional[str] = None,
                cursor: Optional[str] = None, limit: int = 50):
        return self._store.list_cases(review_status="PENDING", failure_type=failure_type,
                                      source_release_id=source_release_id, cursor=cursor, limit=limit)

    def review(self, case_id: str, decision: str, reviewer_role: str, *, base_version: Optional[int] = None,
               failure_type: Optional[str] = None, expected: Optional[dict] = None, note: Optional[str] = None) -> dict:
        if decision not in ("CONFIRMED", "DISMISSED"):
            raise BoundaryError("INVALID_RECORD", "decision은 CONFIRMED 또는 DISMISSED")
        if decision == "DISMISSED" and expected is not None:
            raise BoundaryError("INVALID_RECORD", "DISMISSED에는 expected를 둘 수 없다")
        cur = self._store.get_case(case_id)
        if base_version is not None and base_version != cur["version"]:
            raise BoundaryError("CONFLICT", f"최신 version은 {cur['version']}")
        new = {k: v for k, v in cur.items() if k != "ext"}
        new["version"] = cur["version"] + 1
        new["review_status"] = decision
        if failure_type:
            new["failure_type"] = failure_type
        if expected is not None:
            new["expected"] = expected
        ext = dict(cur.get("ext") or {})                 # 다른 영역의 ext 보존
        mine = dict(ext.get("ops-feedback") or {})
        mine["review"] = {"reviewer_role": reviewer_role, **({"note": note} if note else {})}
        ext["ops-feedback"] = mine
        new["ext"] = ext
        # 같은 검토를 다시 보내면(재시도) 새 version을 만들지 않는다.
        if (cur["review_status"] == decision and cur.get("failure_type") == new["failure_type"]
                and cur.get("expected") == new.get("expected") and cur.get("ext") == ext):
            return cur
        new["created_at"] = self._clock()
        return self._store.publish_case(new)

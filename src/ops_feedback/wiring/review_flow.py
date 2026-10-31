"""사람 검토의 조립: 사례 읽기(저장소) -> 검토 적용(review_decider) -> 새 version 게시(저장소). 검토 큐 목록도 여기서 저장소에서 읽는다."""
from __future__ import annotations

from typing import Optional

from ..components.failure_case_store import FailureCaseStore
from ..components.review_decider import ReviewDecider
from . import deadline
from .clock import Clock, utc_now


class ReviewFlow:
    def __init__(self, store: FailureCaseStore, *, clock: Clock = utc_now, residual_patterns: Optional[dict] = None) -> None:
        self._store, self._clock = store, clock
        self._decider = ReviewDecider(residual_patterns=residual_patterns)

    def pending(self, *, failure_type: Optional[str] = None, source_release_id: Optional[str] = None, cursor: Optional[str] = None, limit: int = 50):
        return self._store.list_cases(review_status="PENDING", failure_type=failure_type, source_release_id=source_release_id, cursor=cursor, limit=limit)

    def review_with_status(self, case_id: str, decision: str, reviewer_role: str, *, base_version: Optional[int] = None, failure_type: Optional[str] = None,
                           expected=None, expected_kind: Optional[str] = None, reviewed_input=None, note: Optional[str] = None,
                           reviewed_by: Optional[str] = None) -> tuple[dict, bool]:
        """(레코드, 새 version이면 True). 같은 검토를 다시 보내면 기존 레코드와 False."""
        d: dict = {"review_status": decision, "reviewer_role": reviewer_role}
        for k, v in (("base_version", base_version), ("failure_type", failure_type), ("expected", expected), ("expected_kind", expected_kind),
                     ("reviewed_input", reviewed_input), ("note", note), ("reviewed_by", reviewed_by)):
            if v is not None:
                d[k] = v
        deadline.check_current()                                  # 요청 전체 마감(D120): 단계마다 남은 시간을 본다
        cur = self._store.get_case(case_id)                       # 없으면 NOT_FOUND
        deadline.check_current()
        out = self._decider.decide({"current": cur, "decision": d, "now": self._clock()})
        if not out["changed"]:
            return out["case"], False
        deadline.check_current()                                  # 쓰기는 이 한 문장뿐이다: 그 전에 멈추면 반쯤 쓴 상태가 없다
        rec = self._store.publish_case(out["case"])
        return rec, rec["version"] > cur["version"]

    def review(self, case_id: str, decision: str, reviewer_role: str, **kw) -> dict:
        return self.review_with_status(case_id, decision, reviewer_role, **kw)[0]

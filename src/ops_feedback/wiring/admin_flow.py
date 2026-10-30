"""관리 도구의 조립: 세션 삭제(erasure_planner -> 저장소 -> erasure_summary)와 purge(purge_planner -> 저장소). 인자·출력은 admin/__main__.py가 맡는다."""
from __future__ import annotations

from typing import Optional

from ..components import erasure_planner, erasure_summary, purge_planner
from ..components.failure_case_store import FailureCaseStore
from ..components.pseudonymizer import pseudonymize
from .clock import utc_now
from .log_store import LogStore


def session_pseudonym(salt: str, session_id: str) -> str:
    """비식별 처리기가 session_id 키에 붙이는 의사 식별자. 식은 pseudonymizer 컴포넌트 한 곳에만 있다."""
    return pseudonymize({"salt": salt, "key": "session_id", "value": session_id})


def delete_session(log: LogStore, fail: Optional[FailureCaseStore], salt: str, session_id: str, dry_run: bool) -> dict:
    counts = {"raw_rows": 0, "cases": 0, "traces": 0, "dry_run": dry_run}
    keys = erasure_planner.plan({"session_id": session_id, "session_pseudonym": session_pseudonym(salt, session_id)})["trace_session_keys"]
    refs = sorted({r for k in keys for r in fail.traces_of_session(k)}) if fail is not None else []
    if dry_run:
        counts["traces"] = len(refs)
        counts["raw_rows"] = log.count_session(session_id)       # 실제 삭제와 같은 대상: 아직 비식별되지 않은 세션은 원본 행만 있다
    else:
        counts["raw_rows"] = log.delete_session(session_id)
        if fail is not None and refs:
            r = fail.delete_by_trace_refs(refs)
            counts.update(cases=r["cases"], traces=r["traces"])
    return erasure_summary.summarize(counts)


def purge(log: LogStore, fail: Optional[FailureCaseStore], **days) -> dict:
    """days: raw_days, cases_days, metrics_days(주지 않은 것은 지우지 않는다), include_confirmed, include_unprocessed."""
    cut = purge_planner.plan({"now": utc_now(), "include_confirmed": days.pop("include_confirmed", False),
                              "include_unprocessed": days.pop("include_unprocessed", False), **{k: v for k, v in days.items() if v is not None}})
    out = {}
    if cut["raw_cutoff"] is not None:
        out["raw"] = log.purge_received_before(cut["raw_cutoff"], processed_only=not cut["include_unprocessed"])
    if cut["cases_cutoff"] is not None and fail is not None:
        out["cases"] = fail.purge_cases_created_before(cut["cases_cutoff"], include_confirmed=cut["include_confirmed"])
    if cut["metrics_cutoff"] is not None and fail is not None:
        out["aux"] = fail.purge_aux_before(cut["metrics_cutoff"])
    return out

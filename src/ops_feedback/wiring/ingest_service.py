"""ingest-api 통신 컴포넌트(link_ingest_api)에 주입하는 서비스 면. 요청을 업무 컴포넌트에 잇는 조립이다(통신 컴포넌트는 업무 컴포넌트를 모른다)."""
from __future__ import annotations

from typing import Optional

from ..components.failure_case_store import FailureCaseStore
from .clock import parse_ts, utc_now
from .log_store import LogStore, supported_trace_versions
from .review_flow import ReviewFlow


class IngestService:
    def __init__(self, log: LogStore, failures: Optional[FailureCaseStore] = None, *, clock=utc_now) -> None:
        self._log, self._failures, self._clock = log, failures, clock
        self._flow = ReviewFlow(failures, clock=clock) if failures is not None else None

    # 수집 면
    def send(self, envelope: dict) -> dict:
        return self._log.send(envelope)

    def pings(self) -> list:
        return [self._log.ping] + ([self._failures.ping] if self._failures is not None else [])

    def log_kind(self) -> str:
        return self._log.backend_kind()

    def failure_kind(self) -> Optional[str]:
        return self._failures.backend_kind() if self._failures is not None else None

    def supported_versions(self) -> list[int]:
        return supported_trace_versions()

    # 읽기 면(I7)
    @property
    def read_enabled(self) -> bool:
        return self._failures is not None

    def get_case(self, case_id: str, version: Optional[int]) -> dict:
        return self._failures.get_case(case_id, version=version)

    def list_cases(self, *, review_status, failure_type, source_release_id, updated_since: Optional[str], latest_only: bool, cursor, limit: int):
        return self._failures.list_cases(review_status=review_status, failure_type=failure_type, source_release_id=source_release_id,
                                         updated_since=parse_ts(updated_since) if updated_since else None, latest_only=latest_only, cursor=cursor, limit=limit)

    def get_trace(self, ref: str) -> dict:
        return self._failures.get_trace(ref)

    # 검토 면
    @property
    def review_enabled(self) -> bool:
        return self._flow is not None

    def review(self, case_id: str, body: dict, role: str, user: Optional[str]) -> tuple[dict, bool]:
        """(레코드, 새 version이면 True). 같은 검토를 다시 보내면 기존 레코드와 False."""
        return self._flow.review_with_status(case_id, body["review_status"], role, expected=body.get("expected"), expected_kind=body.get("expected_kind"),
                                             reviewed_input=body.get("reviewed_input"), reviewed_by=user)


def make_ingest_server(log: LogStore, token: str, host: str = "0.0.0.0", port: int = 8080, *, failures: Optional[FailureCaseStore] = None, **kw):
    """수집 서버 조립: 서비스 면(IngestService)을 통신 컴포넌트에 주입한다."""
    from ..components.link_ingest_api import make_server
    return make_server(IngestService(log, failures), token, host, port, **kw)

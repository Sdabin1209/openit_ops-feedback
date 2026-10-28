"""시험용 대역. 이웃 영역(release-gate)의 SDK를 boundary_contracts.sdk의 Protocol에 맞춰 직접 만들었다."""
from __future__ import annotations

from boundary_contracts import PIPELINE_SCHEMA_VERSION

from boundary_contracts.errors import BoundaryError


class FakeReleaseReader:
    """I4 대역. releases는 최신부터."""

    def __init__(self, release_ids: list[str], active: str | None, pointer_version: int = 1) -> None:
        self._ids, self._active, self._pv = release_ids, active, pointer_version

    def get_active_pointer(self):
        if self._active is None:
            return None
        return {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "release_id": self._active, "pointer_version": self._pv,
                "activated_at": "2026-10-15T00:00:00Z", "cause": "ACTIVATE"}

    def get_release(self, release_id):
        if release_id not in self._ids:
            raise BoundaryError("NOT_FOUND", release_id)
        return {"release_id": release_id}

    def load_bundle(self, release_id, *, dest):
        raise BoundaryError("FAILED_PRECONDITION", "대역은 번들을 싣지 않는다")

    def get_decision(self, decision_id):
        raise BoundaryError("NOT_FOUND", decision_id)

    def find_decisions(self, **kw):
        return [], None

    def list_releases(self, *, cursor=None, limit=100):
        start = int(cursor) if cursor else 0
        items = [{"release_id": r} for r in self._ids[start:start + limit]]
        nxt = str(start + limit) if start + limit < len(self._ids) else None
        return items, nxt


class FakeGate:
    """I5 대역(request_rollback만)."""

    def __init__(self, fail_times: int = 0) -> None:
        self.calls: list[dict] = []
        self.fail_times = fail_times

    def submit_candidate(self, subject, *, requested_by_role):
        raise NotImplementedError

    def activate(self, release_id, *, requested_by_role):
        raise NotImplementedError

    def request_rollback(self, request):
        self.calls.append(request)
        if self.fail_times:
            self.fail_times -= 1
            raise BoundaryError("UNAVAILABLE", "게이트 일시 장애")
        return {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "request_id": request["request_id"], "status": "APPLIED",
                "decided_at": "2026-10-15T01:00:00Z",
                "active_pointer": {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "release_id": request["to_release_id"],
                                   "pointer_version": 2, "activated_at": "2026-10-15T01:00:00Z", "cause": "ROLLBACK"}}

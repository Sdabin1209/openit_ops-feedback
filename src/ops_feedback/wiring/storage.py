"""저장소 선택: DATABASE_URL 비밀이 있으면 PostgreSQL(운영), 없으면 SQLite 파일(개발·시험 기본).

URL은 boundary_contracts.read_secret으로 읽는다(NAME_FILE 우선). 값은 이 함수 밖으로 나가지 않는다.
"""
from __future__ import annotations

from typing import Mapping, Optional

from boundary_contracts import read_secret
from boundary_contracts.errors import BoundaryError

from ..components.failure_case_store import FailureCaseStore
from .clock import Clock, utc_now
from .log_store import LogStore

LOG_URL = "OPS_LOG_DATABASE_URL"
FAILURE_URL = "OPS_FAILURE_DATABASE_URL"


def require_verify(env: Mapping[str, str]) -> dict:
    """서버·잡용 환경 사본: OPS_DB_SCHEMA는 verify뿐이다(없으면 verify). apply는 소유자 계정의 `admin migrate`만 한다(ADR 0031)."""
    mode = (env.get("OPS_DB_SCHEMA") or "verify").strip()
    if mode != "verify":
        raise BoundaryError("INVALID_RECORD", "서버·잡은 OPS_DB_SCHEMA=verify만 허용한다. 스키마는 소유자 계정의 `python -m ops_feedback.admin migrate`로 만든다")
    return {**env, "OPS_DB_SCHEMA": "verify"}


def open_log_store(env: Mapping[str, str], sqlite_path: str, *, clock: Clock = utc_now) -> LogStore:
    url = read_secret(LOG_URL, environ=env)
    return LogStore.postgres(url, clock=clock, environ=env) if url else LogStore.sqlite(sqlite_path, clock=clock)


def open_failure_store(env: Mapping[str, str], sqlite_path: Optional[str]) -> Optional[FailureCaseStore]:
    url = read_secret(FAILURE_URL, environ=env)
    if url:
        return FailureCaseStore.postgres(url, environ=env)
    return FailureCaseStore.sqlite(sqlite_path) if sqlite_path else None

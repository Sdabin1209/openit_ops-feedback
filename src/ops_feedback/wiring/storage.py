"""저장소 선택: DATABASE_URL 비밀이 있으면 PostgreSQL(운영), 없으면 SQLite 파일(개발·시험 기본).

URL은 boundary_contracts.read_secret으로 읽는다(NAME_FILE 우선). 값은 이 함수 밖으로 나가지 않는다.
"""
from __future__ import annotations

from typing import Mapping, Optional

from boundary_contracts import read_secret

from ..components.failure_case_store import FailureCaseStore
from .clock import Clock, utc_now
from .log_store import LogStore

LOG_URL = "OPS_LOG_DATABASE_URL"
FAILURE_URL = "OPS_FAILURE_DATABASE_URL"


def open_log_store(env: Mapping[str, str], sqlite_path: str, *, clock: Clock = utc_now) -> LogStore:
    url = read_secret(LOG_URL, environ=env)
    return LogStore.postgres(url, clock=clock) if url else LogStore.sqlite(sqlite_path, clock=clock)


def open_failure_store(env: Mapping[str, str], sqlite_path: Optional[str]) -> Optional[FailureCaseStore]:
    url = read_secret(FAILURE_URL, environ=env)
    if url:
        return FailureCaseStore.postgres(url)
    return FailureCaseStore.sqlite(sqlite_path) if sqlite_path else None

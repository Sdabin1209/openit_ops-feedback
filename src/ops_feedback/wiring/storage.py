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


def require_current_major(log: Optional[LogStore], failures: Optional[FailureCaseStore]) -> None:
    """서버·잡 기동 때: 저장소에 계약 4.x 레코드가 남아 있으면 UNSUPPORTED_VERSION 으로 시작을 거부한다(계약 5.0.0 '이전': 새 환경을 전제하고 4.x 데이터는 이전하지 않는다).
    왜 거부인가: 4.x 사례를 5.0 으로 다시 내보낼 수 없고(ID 형식이 달라졌다) 조용히 섞이면 이웃이 받은 값이 계약을 어긴다. admin(삭제·purge)은 이 검사를 하지 않아 남은 데이터를 지울 수 있다."""
    for what, store in (("로그 저장소", log), ("실패 사례 저장소", failures)):
        if store is not None and store.legacy_major_present():
            raise BoundaryError("UNSUPPORTED_VERSION", f"{what}에 계약 4.x 레코드가 남아 있다. 4.x 데이터는 이전하지 않는다: 새 저장소를 쓰거나 admin purge 로 지운다",
                                {"expected_major": 5})

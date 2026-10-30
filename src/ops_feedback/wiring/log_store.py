"""수집(I6 TraceIngest) 조립: 수집 컴포넌트(trace_intake) -> 로그 저장소(raw_log_store) -> 수집 결과(ingest_receipt).
원본 읽기는 `grant_deidentifier_view()`로만 열린다(비식별 단계에 넘기는 조립 지점 한 곳, tests/test_raw_access.py)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import survey_schema

from ..components import ingest_receipt, trace_intake
from ..components.raw_log_store import DeidentifierView, RawLogStore
from .clock import Clock, utc_now


def supported_trace_versions() -> list[int]:
    """문진 스키마 패키지(openit-survey-schema)가 낸 정수 버전들. 사본을 두지 않고 설치된 패키지를 읽는다."""
    return survey_schema.available_versions()


class LogStore:
    """TraceIngest(I6)의 구현. 저장소 백엔드만 SQLite·PostgreSQL·메모리로 갈린다."""

    FAILED_STATUSES = RawLogStore.FAILED_STATUSES

    def __init__(self, raw: RawLogStore, *, clock: Clock = utc_now) -> None:
        self._raw = raw
        self._clock = clock

    @classmethod
    def memory(cls, *, clock: Clock = utc_now) -> "LogStore":
        return cls(RawLogStore.memory(), clock=clock)

    @classmethod
    def sqlite(cls, path: str | Path, *, clock: Clock = utc_now) -> "LogStore":
        return cls(RawLogStore.sqlite(path), clock=clock)

    @classmethod
    def postgres(cls, url: str, *, clock: Clock = utc_now) -> "LogStore":
        return cls(RawLogStore.postgres(url), clock=clock)

    # --- I6 ---
    def send(self, envelope: dict) -> dict:
        record = trace_intake.accept({"envelope": envelope, "received_at": self._clock(), "supported_versions": supported_trace_versions()})
        is_new = self._raw.insert_if_absent(record)
        first = None if is_new else self._raw.first_received_at(record["session_id"], record["turn_id"])
        req = {"record": record, "is_new": is_new}
        if first is not None:
            req["first_received_at"] = first
        return ingest_receipt.receipt(req)

    # --- 원본이 아닌 메타데이터 (읽기 허용) ---
    def backend_kind(self) -> str:
        return self._raw.backend_kind()

    def counts_by_release(self, since: Optional[str] = None) -> dict[str, int]:
        return self._raw.counts_by_release(since)

    def mark(self, session_id: str, turn_id: str, status: str, config_hash: str, processed_at: str) -> None:
        self._raw.mark({"session_id": session_id, "turn_id": turn_id, "status": status, "config_hash": config_hash, "processed_at": processed_at})

    def reset_processing(self, only_status: Optional[str] = None) -> int:
        return self._raw.reset_processing(only_status)

    def purge_received_before(self, cutoff: str, processed_only: bool = False) -> int:
        return self._raw.purge_received_before(cutoff, processed_only)

    def purge_failed_before(self, cutoff: str) -> int:
        return self._raw.purge_failed_before(cutoff)

    def minimize_processed(self, received_before: Optional[str] = None) -> int:
        return self._raw.minimize_processed(received_before)

    def delete_session(self, session_id: str) -> int:
        return self._raw.delete_session(session_id)

    def count_session(self, session_id: str) -> int:
        return self._raw.count_session(session_id)

    def ping(self) -> None:
        self._raw.ping()

    def grant_deidentifier_view(self) -> DeidentifierView:
        """비식별 단계(wiring/pipeline.py)에만 건넨다."""
        return self._raw.grant_deidentifier_view()

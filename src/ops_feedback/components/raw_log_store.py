"""로그 저장소(원본). 입력 RawTraceRecord(수집 기록)·RawStatusMark(처리 표시), 출력은 저장된 기록.

원본(TraceEnvelope 전체)은 비식별 단계만 읽는다(D15). 읽기는 `DeidentifierView`로만 열리고, 그 권한을 비식별 단계에 넘기는 곳은 wiring 한 곳이다
(tests/test_raw_access.py가 다른 모듈의 접근을 막는다).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator, Optional

from boundary_contracts.errors import BoundaryError

from ..migrate import migrate_sqlite
from ..types.RawStatusMark import RawStatusMark
from ..types.RawTraceRecord import RawTraceRecord
from ..wiring.pgbase import PgBase
from ..wiring.validate import check


MINIMIZED = '{"minimized": true}'          # 처리가 끝난 원본의 봉투 자리에 남기는 표지(중복 판별 키·해시·시각·릴리스는 남는다)
MINIMIZABLE = ("DONE_CASE", "NO_FAILURE")  # 재처리가 필요한 상태(DEID_FAILED, PUBLISH_FAILED)는 원본을 남긴다


class _RawBackend(ABC):
    @abstractmethod
    def insert_if_absent(self, session_id: str, turn_id: str, release_id: str, received_at: str,
                         envelope: dict, envelope_hash: str) -> bool: ...
    @abstractmethod
    def unprocessed(self, limit: Optional[int], after_seq: int = 0) -> list[dict]: ...
    @abstractmethod
    def minimize_processed(self, received_before: Optional[str]) -> int: ...
    @abstractmethod
    def delete_session(self, session_id: str) -> int: ...
    @abstractmethod
    def count_session(self, session_id: str) -> int: ...
    @abstractmethod
    def mark(self, session_id: str, turn_id: str, status: str, config_hash: str, processed_at: str) -> None: ...
    @abstractmethod
    def reset_processing(self, only_status: Optional[str]) -> int: ...
    @abstractmethod
    def counts_by_release(self, since: Optional[str]) -> dict[str, int]: ...
    @abstractmethod
    def purge_received_before(self, cutoff: str, statuses: Optional[tuple] = None) -> int: ...
    @abstractmethod
    def ping(self) -> None: ...
    @abstractmethod
    def received_at(self, session_id: str, turn_id: str) -> Optional[str]: ...


class MemoryBackend(_RawBackend):
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], dict] = {}
        self._lock = threading.Lock()

    def insert_if_absent(self, session_id, turn_id, release_id, received_at, envelope, envelope_hash) -> bool:
        with self._lock:
            k = (session_id, turn_id)
            if k in self._rows:
                return False
            self._rows[k] = {"session_id": session_id, "turn_id": turn_id, "release_id": release_id,
                             "received_at": received_at, "envelope": json.loads(json.dumps(envelope)),
                             "envelope_hash": envelope_hash, "status": None, "seq": len(self._rows) + 1}
            return True

    def minimize_processed(self, received_before):
        with self._lock:
            n = 0
            for r in self._rows.values():
                if r["status"] in MINIMIZABLE and r["envelope"] != {"minimized": True} and (received_before is None or r["received_at"] < received_before):
                    r["envelope"] = {"minimized": True}
                    n += 1
            return n

    def count_session(self, session_id):
        with self._lock:
            return sum(1 for k in self._rows if k[0] == session_id)

    def delete_session(self, session_id):
        with self._lock:
            old = [k for k in self._rows if k[0] == session_id]
            for k in old:
                del self._rows[k]
            return len(old)

    def unprocessed(self, limit, after_seq=0):
        with self._lock:
            rows = sorted((r for r in self._rows.values() if r["status"] is None and r["seq"] > after_seq), key=lambda r: r["seq"])
            rows = rows[:limit] if limit else rows
            return [json.loads(json.dumps(r)) for r in rows]

    def mark(self, session_id, turn_id, status, config_hash, processed_at):
        with self._lock:
            self._rows[(session_id, turn_id)]["status"] = status

    def reset_processing(self, only_status):
        with self._lock:
            n = 0
            for r in self._rows.values():
                if r["status"] is not None and r["envelope"] != {"minimized": True} and (only_status is None or r["status"] == only_status):
                    r["status"] = None
                    n += 1
            return n

    def counts_by_release(self, since):
        with self._lock:
            out: dict[str, int] = {}
            for r in self._rows.values():
                if since is None or r["received_at"] >= since:
                    out[r["release_id"]] = out.get(r["release_id"], 0) + 1
            return out

    def purge_received_before(self, cutoff, statuses=None):
        with self._lock:
            old = [k for k, r in self._rows.items() if r["received_at"] < cutoff and (statuses is None or r["status"] in statuses)]
            for k in old:
                del self._rows[k]
            return len(old)

    def ping(self):
        return None

    def received_at(self, session_id, turn_id):
        r = self._rows.get((session_id, turn_id))
        return r["received_at"] if r else None


class SqliteBackend(_RawBackend):
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        migrate_sqlite("log", self._path)       # 스키마는 마이그레이션(yoyo)이 만든다(ADR 0021)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")

    def _x(self, fn):
        try:
            with self._lock:
                return fn()
        except sqlite3.Error as e:
            raise BoundaryError("UNAVAILABLE", f"로그 저장소 오류: {type(e).__name__}") from e

    def insert_if_absent(self, session_id, turn_id, release_id, received_at, envelope, envelope_hash) -> bool:
        def f():
            cur = self._db.execute(
                "INSERT OR IGNORE INTO raw_traces(session_id,turn_id,release_id,received_at,envelope,envelope_hash)"
                " VALUES(?,?,?,?,?,?)",
                (session_id, turn_id, release_id, received_at, json.dumps(envelope, ensure_ascii=False), envelope_hash))
            return cur.rowcount == 1
        return self._x(f)

    def minimize_processed(self, received_before):
        q, a = "UPDATE raw_traces SET envelope=? WHERE status IN ('DONE_CASE','NO_FAILURE') AND envelope<>?", [MINIMIZED, MINIMIZED]
        if received_before is not None:
            q, a = q + " AND received_at<?", a + [received_before]
        return self._x(lambda: self._db.execute(q, a).rowcount)

    def count_session(self, session_id):
        return self._x(lambda: self._db.execute("SELECT COUNT(*) FROM raw_traces WHERE session_id=?", (session_id,)).fetchone()[0])

    def delete_session(self, session_id):
        return self._x(lambda: self._db.execute("DELETE FROM raw_traces WHERE session_id=?", (session_id,)).rowcount)

    def unprocessed(self, limit, after_seq=0):
        def f():
            q = "SELECT session_id,turn_id,release_id,received_at,envelope,envelope_hash,seq FROM raw_traces WHERE status IS NULL AND seq>? ORDER BY seq"
            args: tuple = (after_seq,)
            if limit:
                q += " LIMIT ?"
                args = (after_seq, limit)
            return [{"session_id": a, "turn_id": b, "release_id": c, "received_at": d, "envelope": json.loads(e),
                     "envelope_hash": h, "seq": s, "status": None} for a, b, c, d, e, h, s in self._db.execute(q, args)]
        return self._x(f)

    def mark(self, session_id, turn_id, status, config_hash, processed_at):
        self._x(lambda: self._db.execute(
            "UPDATE raw_traces SET status=?, config_hash=?, processed_at=? WHERE session_id=? AND turn_id=?",
            (status, config_hash, processed_at, session_id, turn_id)))

    def reset_processing(self, only_status):
        def f():
            if only_status is None:
                return self._db.execute("UPDATE raw_traces SET status=NULL WHERE status IS NOT NULL AND envelope<>?", (MINIMIZED,)).rowcount
            return self._db.execute("UPDATE raw_traces SET status=NULL WHERE status=? AND envelope<>?", (only_status, MINIMIZED)).rowcount
        return self._x(f)

    def counts_by_release(self, since):
        def f():
            if since is None:
                rows = self._db.execute("SELECT release_id,COUNT(*) FROM raw_traces GROUP BY release_id")
            else:
                rows = self._db.execute("SELECT release_id,COUNT(*) FROM raw_traces WHERE received_at>=? GROUP BY release_id", (since,))
            return {r: n for r, n in rows}
        return self._x(f)

    def purge_received_before(self, cutoff, statuses=None):
        q = "DELETE FROM raw_traces WHERE received_at<?" + ("" if statuses is None else " AND status IN (" + ",".join([ "?"] * len(statuses)) + ")")
        return self._x(lambda: self._db.execute(q, (cutoff, *(statuses or ()))).rowcount)

    def ping(self):
        self._x(lambda: self._db.execute("SELECT 1").fetchone())

    def received_at(self, session_id, turn_id):
        row = self._x(lambda: self._db.execute("SELECT received_at FROM raw_traces WHERE session_id=? AND turn_id=?",
                                               (session_id, turn_id)).fetchone())
        return row[0] if row else None

    KIND = "sqlite"

    def close(self) -> None:
        self._db.close()


class PostgresRawBackend(PgBase, _RawBackend):
    WHAT = "로그 저장소"
    TABLES = ("raw_traces",)
    SCOPE = "log"

    def insert_if_absent(self, session_id, turn_id, release_id, received_at, envelope, envelope_hash) -> bool:
        return self._x(lambda: self._db.execute(
            "INSERT INTO raw_traces(session_id,turn_id,release_id,received_at,envelope,envelope_hash)"
            " VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(session_id,turn_id) DO NOTHING",
            (session_id, turn_id, release_id, received_at, json.dumps(envelope, ensure_ascii=False), envelope_hash)).rowcount == 1)

    def minimize_processed(self, received_before):
        q, a = "UPDATE raw_traces SET envelope=%s WHERE status IN ('DONE_CASE','NO_FAILURE') AND envelope<>%s", [MINIMIZED, MINIMIZED]
        if received_before is not None:
            q, a = q + " AND received_at<%s", a + [received_before]
        return self._x(lambda: self._db.execute(q, a).rowcount)

    def count_session(self, session_id):
        return self._x(lambda: self._db.execute("SELECT COUNT(*) FROM raw_traces WHERE session_id=%s", (session_id,)).fetchone()[0])

    def delete_session(self, session_id):
        return self._x(lambda: self._db.execute("DELETE FROM raw_traces WHERE session_id=%s", (session_id,)).rowcount)

    def unprocessed(self, limit, after_seq=0):
        def f():
            q = "SELECT session_id,turn_id,release_id,received_at,envelope,envelope_hash,seq FROM raw_traces WHERE status IS NULL AND seq>%s ORDER BY seq"
            args: tuple = (after_seq,)
            if limit:
                q += " LIMIT %s"
                args = (after_seq, limit)
            return [{"session_id": a, "turn_id": b, "release_id": c, "received_at": d, "envelope": json.loads(e),
                     "envelope_hash": h, "seq": s, "status": None} for a, b, c, d, e, h, s in self._db.execute(q, args).fetchall()]
        return self._x(f)

    def mark(self, session_id, turn_id, status, config_hash, processed_at):
        self._x(lambda: self._db.execute(
            "UPDATE raw_traces SET status=%s, config_hash=%s, processed_at=%s WHERE session_id=%s AND turn_id=%s",
            (status, config_hash, processed_at, session_id, turn_id)))

    def reset_processing(self, only_status):
        def f():
            if only_status is None:
                return self._db.execute("UPDATE raw_traces SET status=NULL WHERE status IS NOT NULL AND envelope<>%s", (MINIMIZED,)).rowcount
            return self._db.execute("UPDATE raw_traces SET status=NULL WHERE status=%s AND envelope<>%s", (only_status, MINIMIZED)).rowcount
        return self._x(f)

    def counts_by_release(self, since):
        def f():
            if since is None:
                rows = self._db.execute("SELECT release_id,COUNT(*) FROM raw_traces GROUP BY release_id")
            else:
                rows = self._db.execute("SELECT release_id,COUNT(*) FROM raw_traces WHERE received_at>=%s GROUP BY release_id", (since,))
            return {r: int(n) for r, n in rows.fetchall()}
        return self._x(f)

    def purge_received_before(self, cutoff, statuses=None):
        q = "DELETE FROM raw_traces WHERE received_at<%s" + ("" if statuses is None else " AND status IN (" + ",".join([ "%s"] * len(statuses)) + ")")
        return self._x(lambda: self._db.execute(q, (cutoff, *(statuses or ()))).rowcount)

    def received_at(self, session_id, turn_id):
        row = self._x(lambda: self._db.execute("SELECT received_at FROM raw_traces WHERE session_id=%s AND turn_id=%s",
                                               (session_id, turn_id)).fetchone())
        return row[0] if row else None


class DeidentifierView:
    """원본 읽기 권한. wiring이 비식별 단계에만 건넨다."""

    def __init__(self, log: "RawLogStore") -> None:
        self._log = log

    def read_unprocessed(self, limit: Optional[int] = None, after_seq: int = 0) -> list[dict]:
        return self._log._backend.unprocessed(limit, after_seq)


class RawLogStore:
    """로그 저장소. 백엔드만 SQLite·PostgreSQL·메모리로 갈린다(같은 로직, 같은 적합성 시험)."""

    def __init__(self, backend: _RawBackend) -> None:
        self._backend = backend

    @classmethod
    def memory(cls) -> "RawLogStore":
        return cls(MemoryBackend())

    @classmethod
    def sqlite(cls, path: str | Path) -> "RawLogStore":
        return cls(SqliteBackend(path))

    @classmethod
    def postgres(cls, url: str) -> "RawLogStore":
        return cls(PostgresRawBackend(url))

    def backend_kind(self) -> str:
        return getattr(self._backend, "KIND", "sqlite" if isinstance(self._backend, SqliteBackend) else "memory")

    def insert_if_absent(self, record: RawTraceRecord) -> bool:
        """새로 들어갔으면 True, (session_id, turn_id)가 이미 있으면 False(중복 제거, O04)."""
        check("RawTraceRecord", record)
        return self._backend.insert_if_absent(record["session_id"], record["turn_id"], record["release_id"], record["received_at"],
                                              record["envelope"], record["envelope_hash"])

    def first_received_at(self, session_id: str, turn_id: str) -> Optional[str]:
        return self._backend.received_at(session_id, turn_id)

    # --- 원본이 아닌 메타데이터 (읽기 허용) ---
    def counts_by_release(self, since: Optional[str] = None) -> dict[str, int]:
        return self._backend.counts_by_release(since)

    def mark(self, mark: RawStatusMark) -> None:
        check("RawStatusMark", mark)
        self._backend.mark(mark["session_id"], mark["turn_id"], mark["status"], mark["config_hash"], mark["processed_at"])

    def reset_processing(self, only_status: Optional[str] = None) -> int:
        return self._backend.reset_processing(only_status)

    FAILED_STATUSES = ("DEID_FAILED", "PUBLISH_FAILED")      # 재시도 대상: 일반 보존 삭제에서 제외하고 별도 기한(retention.failed_raw_days)을 쓴다

    def purge_received_before(self, cutoff: str, processed_only: bool = False) -> int:
        """cutoff보다 먼저 받은 원본을 지운다. processed_only이면 처리가 끝난 것(DONE_CASE, NO_FAILURE)만:
        미처리 원본과 재시도 대상(DEID_FAILED, PUBLISH_FAILED)은 남긴다."""
        return self._backend.purge_received_before(cutoff, MINIMIZABLE if processed_only else None)

    def purge_failed_before(self, cutoff: str) -> int:
        """재시도 대상(DEID_FAILED, PUBLISH_FAILED) 원본 중 cutoff보다 먼저 받은 것을 지운다."""
        return self._backend.purge_received_before(cutoff, self.FAILED_STATUSES)

    def minimize_processed(self, received_before: Optional[str] = None) -> int:
        """처리가 끝난(DONE_CASE, NO_FAILURE) 원본의 봉투 내용을 지운다. (session_id, turn_id)·해시·시각·릴리스·처리 표시는 남아
        중복 수집이 다시 ACCEPTED로 들어오지 않고 릴리스별 집계도 유지된다. 재처리(--reprocess)는 이 행들을 건너뛴다."""
        return self._backend.minimize_processed(received_before)

    def delete_session(self, session_id: str) -> int:
        """세션 하나의 원본 행을 모두 지운다(요청 시 삭제). 중복 키도 사라지므로 같은 턴이 다시 오면 새로 받는다."""
        return self._backend.delete_session(session_id)

    def count_session(self, session_id: str) -> int:
        """세션 하나의 원본 행 수. delete_session --dry-run이 실제 삭제와 같은 대상을 세기 위한 것(점검 round3 L3)."""
        return self._backend.count_session(session_id)

    def ping(self) -> None:
        self._backend.ping()

    def grant_deidentifier_view(self) -> DeidentifierView:
        """비식별 단계에 원본 읽기 권한을 건네는 wiring 한 곳에서만 부른다."""
        return DeidentifierView(self)

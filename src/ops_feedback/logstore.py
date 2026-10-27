"""로그 저장소(원본)와 수집(I6 TraceIngest).

원본(TraceEnvelope 전체)은 비식별 처리기만 읽는다(D15). 수집 쪽 인터페이스(`send`)에는 읽기가 없다.
읽기는 `DeidentifierView`로만 열리며, 이 객체를 만드는 코드는 deid.py의 Deidentifier뿐이다
(tests/test_raw_access.py가 다른 모듈의 접근을 막는다).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator, Optional

from boundary_contracts.canonical import sha256_hash
from boundary_contracts.errors import BoundaryError

from .pgbase import PgBase
from .clock import Clock, utc_now
from .schemas import supported_trace_versions, validate_record, validate_trace

from boundary_contracts import PIPELINE_SCHEMA_VERSION  # 새 레코드는 이 버전으로 쓴다(1.1). 읽기는 1.0·1.1 모두


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
    def mark(self, session_id: str, turn_id: str, status: str, config_hash: str, processed_at: str) -> None: ...
    @abstractmethod
    def reset_processing(self, only_status: Optional[str]) -> int: ...
    @abstractmethod
    def counts_by_release(self, since: Optional[str]) -> dict[str, int]: ...
    @abstractmethod
    def purge_received_before(self, cutoff: str, processed_only: bool = False) -> int: ...
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

    def purge_received_before(self, cutoff, processed_only=False):
        with self._lock:
            old = [k for k, r in self._rows.items() if r["received_at"] < cutoff and (not processed_only or r["status"] is not None)]
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
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        if self._path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS raw_traces(
              seq INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT NOT NULL, turn_id TEXT NOT NULL, release_id TEXT NOT NULL,
              received_at TEXT NOT NULL, envelope TEXT NOT NULL, envelope_hash TEXT NOT NULL,
              status TEXT, config_hash TEXT, processed_at TEXT,
              UNIQUE(session_id, turn_id));
            CREATE INDEX IF NOT EXISTS raw_status ON raw_traces(status, seq);
            CREATE INDEX IF NOT EXISTS raw_release ON raw_traces(release_id, received_at);
        """)

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

    def purge_received_before(self, cutoff, processed_only=False):
        q = "DELETE FROM raw_traces WHERE received_at<?" + (" AND status IS NOT NULL" if processed_only else "")
        return self._x(lambda: self._db.execute(q, (cutoff,)).rowcount)

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
    DDL = """
        CREATE TABLE IF NOT EXISTS raw_traces(
          seq BIGSERIAL PRIMARY KEY,
          session_id TEXT NOT NULL, turn_id TEXT NOT NULL, release_id TEXT NOT NULL,
          received_at TEXT NOT NULL, envelope TEXT NOT NULL, envelope_hash TEXT NOT NULL,
          status TEXT, config_hash TEXT, processed_at TEXT,
          UNIQUE(session_id, turn_id));
        CREATE INDEX IF NOT EXISTS raw_status ON raw_traces(status, seq);
        CREATE INDEX IF NOT EXISTS raw_release ON raw_traces(release_id, received_at);
    """

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

    def purge_received_before(self, cutoff, processed_only=False):
        q = "DELETE FROM raw_traces WHERE received_at<%s" + (" AND status IS NOT NULL" if processed_only else "")
        return self._x(lambda: self._db.execute(q, (cutoff,)).rowcount)

    def received_at(self, session_id, turn_id):
        row = self._x(lambda: self._db.execute("SELECT received_at FROM raw_traces WHERE session_id=%s AND turn_id=%s",
                                               (session_id, turn_id)).fetchone())
        return row[0] if row else None


class DeidentifierView:
    """원본 읽기 권한. 비식별 처리기(deid.Deidentifier)만 만든다."""

    def __init__(self, log: "LogStore") -> None:
        self._log = log

    def read_unprocessed(self, limit: Optional[int] = None, after_seq: int = 0) -> list[dict]:
        return self._log._backend.unprocessed(limit, after_seq)


class LogStore:
    """TraceIngest(I6)의 구현. 백엔드만 SQLite와 메모리로 갈린다(같은 로직, 같은 적합성 시험)."""

    def __init__(self, backend: _RawBackend, *, clock: Clock = utc_now) -> None:
        self._backend = backend
        self._clock = clock

    @classmethod
    def memory(cls, *, clock: Clock = utc_now) -> "LogStore":
        return cls(MemoryBackend(), clock=clock)

    @classmethod
    def sqlite(cls, path: str | Path, *, clock: Clock = utc_now) -> "LogStore":
        return cls(SqliteBackend(path), clock=clock)

    @classmethod
    def postgres(cls, url: str, *, clock: Clock = utc_now) -> "LogStore":
        return cls(PostgresRawBackend(url), clock=clock)

    def backend_kind(self) -> str:
        return getattr(self._backend, "KIND", "sqlite" if isinstance(self._backend, SqliteBackend) else "memory")

    # --- I6 ---
    def send(self, envelope: dict) -> dict:
        validate_record("TraceEnvelope", envelope)
        ver = envelope["runtime_contract_version"]
        if ver not in supported_trace_versions():
            raise BoundaryError("UNSUPPORTED_VERSION", f"지원하지 않는 문진 스키마 버전: {ver}",
                                {"supported": supported_trace_versions()})
        trace = envelope["trace"]
        validate_trace(ver, trace)
        sid, tid = envelope["session_id"], envelope["turn_id"]
        if trace.get("session_id") != sid or trace.get("turn_id") != tid:
            raise BoundaryError("INVALID_RECORD", "envelope와 trace의 session_id/turn_id가 다르다")
        if trace.get("contract_version") != ver:
            raise BoundaryError("INVALID_RECORD", "trace.contract_version이 envelope.runtime_contract_version과 다르다")
        received_at = self._clock()
        is_new = self._backend.insert_if_absent(sid, tid, envelope["release_id"], received_at, envelope, sha256_hash(envelope))
        result = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "session_id": sid, "turn_id": tid,
                  "status": "ACCEPTED" if is_new else "DUPLICATE", "received_at": received_at}
        if not is_new:
            result["received_at"] = self._first_received_at(sid, tid) or received_at
        validate_record("IngestResult", result)
        return result

    def _first_received_at(self, sid: str, tid: str) -> Optional[str]:
        return self._backend.received_at(sid, tid)

    # --- 원본이 아닌 메타데이터 (읽기 허용) ---
    def counts_by_release(self, since: Optional[str] = None) -> dict[str, int]:
        return self._backend.counts_by_release(since)

    def mark(self, session_id: str, turn_id: str, status: str, config_hash: str, processed_at: str) -> None:
        self._backend.mark(session_id, turn_id, status, config_hash, processed_at)

    def reset_processing(self, only_status: Optional[str] = None) -> int:
        return self._backend.reset_processing(only_status)

    def purge_received_before(self, cutoff: str, processed_only: bool = False) -> int:
        """cutoff보다 먼저 받은 원본을 지운다. processed_only이면 처리 표시가 있는 것만(미처리 원본은 남긴다)."""
        return self._backend.purge_received_before(cutoff, processed_only)

    def minimize_processed(self, received_before: Optional[str] = None) -> int:
        """처리가 끝난(DONE_CASE, NO_FAILURE) 원본의 봉투 내용을 지운다. (session_id, turn_id)·해시·시각·릴리스·처리 표시는 남아
        중복 수집이 다시 ACCEPTED로 들어오지 않고 릴리스별 집계도 유지된다. 재처리(--reprocess)는 이 행들을 건너뛴다."""
        return self._backend.minimize_processed(received_before)

    def delete_session(self, session_id: str) -> int:
        """세션 하나의 원본 행을 모두 지운다(요청 시 삭제). 중복 키도 사라지므로 같은 턴이 다시 오면 새로 받는다."""
        return self._backend.delete_session(session_id)

    def ping(self) -> None:
        self._backend.ping()

    def grant_deidentifier_view(self) -> DeidentifierView:
        """deid.Deidentifier 전용. 다른 곳에서 부르지 않는다."""
        return DeidentifierView(self)

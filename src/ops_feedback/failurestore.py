"""실패 사례 저장소(I7 FailureStore). 로직은 한 곳에 두고 백엔드(SQLite, 메모리)만 갈린다.

여기에는 비식별을 마친 것만 들어온다. 원본은 이 모듈에 오지 않는다.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from boundary_contracts.canonical import sha256_hash
from boundary_contracts.errors import BoundaryError

from .pgbase import PgBase
from .clock import fmt_ts, parse_ts
from .schemas import validate_record


class _Backend(ABC):
    @abstractmethod
    def insert_case(self, record: dict) -> int: ...
    @abstractmethod
    def versions(self, case_id: str) -> list[dict]: ...      # version 오름차순
    @abstractmethod
    def scan(self, after_seq: int) -> Iterator[tuple[int, dict]]: ...   # seq 오름차순
    @abstractmethod
    def query(self, after_seq: int, latest_only: bool, review_status: Optional[str], failure_type: Optional[str],
              source_release_id: Optional[str], since: Optional[str], limit: int) -> list[tuple[int, dict]]: ...
    @abstractmethod
    def put_trace(self, ref: str, trace: dict) -> None: ...
    @abstractmethod
    def get_trace(self, ref: str) -> Optional[dict]: ...
    @abstractmethod
    def put_aux(self, kind: str, key: str, record: dict) -> bool: ...   # 새로 썼으면 True
    @abstractmethod
    def get_aux(self, kind: str, key: str) -> Optional[dict]: ...
    @abstractmethod
    def list_aux(self, kind: str) -> list[dict]: ...
    @abstractmethod
    def ping(self) -> None: ...


class MemoryBackend(_Backend):
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cases: list[tuple[int, dict]] = []
        self._traces: dict[str, dict] = {}
        self._aux: dict[tuple[str, str], dict] = {}

    def insert_case(self, record):
        with self._lock:
            seq = len(self._cases) + 1
            self._cases.append((seq, json.loads(json.dumps(record))))
            return seq

    def versions(self, case_id):
        with self._lock:
            return sorted((json.loads(json.dumps(r)) for _, r in self._cases if r["case_id"] == case_id), key=lambda r: r["version"])

    def scan(self, after_seq):
        with self._lock:
            snap = [(s, json.loads(json.dumps(r))) for s, r in self._cases if s > after_seq]
        yield from snap

    def query(self, after_seq, latest_only, review_status, failure_type, source_release_id, since, limit):
        with self._lock:
            latest: dict[str, int] = {}
            for seq, r in self._cases:
                latest[r["case_id"]] = max(latest.get(r["case_id"], 0), r["version"])
            out = []
            for seq, r in self._cases:
                if seq <= after_seq or (latest_only and r["version"] != latest[r["case_id"]]):
                    continue
                if review_status and r["review_status"] != review_status: continue
                if failure_type and r["failure_type"] != failure_type: continue
                if source_release_id and r["source_release_id"] != source_release_id: continue
                if since and r["created_at"] < since: continue
                out.append((seq, json.loads(json.dumps(r))))
                if len(out) >= limit:
                    break
            return out

    def put_trace(self, ref, trace):
        with self._lock:
            self._traces.setdefault(ref, json.loads(json.dumps(trace)))

    def get_trace(self, ref):
        with self._lock:
            t = self._traces.get(ref)
            return json.loads(json.dumps(t)) if t is not None else None

    def put_aux(self, kind, key, record):
        with self._lock:
            if (kind, key) in self._aux:
                return False
            self._aux[(kind, key)] = json.loads(json.dumps(record))
            return True

    def get_aux(self, kind, key):
        with self._lock:
            r = self._aux.get((kind, key))
            return json.loads(json.dumps(r)) if r is not None else None

    def list_aux(self, kind):
        with self._lock:
            return [json.loads(json.dumps(v)) for (k, _), v in sorted(self._aux.items()) if k == kind]

    def ping(self):
        return None


class SqliteBackend(_Backend):
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        if self._path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS cases(
              seq INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL, version INTEGER NOT NULL,
              review_status TEXT NOT NULL, failure_type TEXT NOT NULL, source_release_id TEXT NOT NULL,
              created_at TEXT NOT NULL, record TEXT NOT NULL, UNIQUE(case_id, version));
            CREATE INDEX IF NOT EXISTS cases_filter ON cases(review_status, failure_type, source_release_id);
            CREATE INDEX IF NOT EXISTS cases_release ON cases(source_release_id, created_at);
            CREATE TABLE IF NOT EXISTS traces(ref TEXT PRIMARY KEY, trace TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS aux(kind TEXT NOT NULL, key TEXT NOT NULL, record TEXT NOT NULL, PRIMARY KEY(kind, key));
        """)

    def _x(self, fn):
        try:
            with self._lock:
                return fn()
        except sqlite3.Error as e:
            raise BoundaryError("UNAVAILABLE", f"실패 사례 저장소 오류: {type(e).__name__}") from e

    def insert_case(self, record):       # 이미 있으면 무시한다. publish_case가 저장된 내용을 대조해 CONFLICT를 낸다
        return self._x(lambda: self._db.execute(
            "INSERT OR IGNORE INTO cases(case_id,version,review_status,failure_type,source_release_id,created_at,record) VALUES(?,?,?,?,?,?,?)",
            (record["case_id"], record["version"], record["review_status"], record["failure_type"],
             record["source_release_id"], record["created_at"], json.dumps(record, ensure_ascii=False))).lastrowid)

    def versions(self, case_id):
        return self._x(lambda: [json.loads(r) for (r,) in self._db.execute(
            "SELECT record FROM cases WHERE case_id=? ORDER BY version", (case_id,))])

    def scan(self, after_seq):
        rows = self._x(lambda: self._db.execute("SELECT seq,record FROM cases WHERE seq>? ORDER BY seq", (after_seq,)).fetchall())
        for s, r in rows:
            yield s, json.loads(r)

    def query(self, after_seq, latest_only, review_status, failure_type, source_release_id, since, limit):
        where, args = ["c.seq>?"], [after_seq]
        if latest_only:
            where.append("NOT EXISTS (SELECT 1 FROM cases d WHERE d.case_id=c.case_id AND d.version>c.version)")
        for col, v in (("review_status", review_status), ("failure_type", failure_type), ("source_release_id", source_release_id)):
            if v:
                where.append(f"c.{col}=?")
                args.append(v)
        if since:
            where.append("c.created_at>=?")
            args.append(since)
        q = f"SELECT c.seq,c.record FROM cases c WHERE {' AND '.join(where)} ORDER BY c.seq LIMIT ?"
        rows = self._x(lambda: self._db.execute(q, (*args, limit)).fetchall())
        return [(s_, json.loads(r)) for s_, r in rows]

    def put_trace(self, ref, trace):
        self._x(lambda: self._db.execute("INSERT OR IGNORE INTO traces(ref,trace) VALUES(?,?)", (ref, json.dumps(trace, ensure_ascii=False))))

    def get_trace(self, ref):
        row = self._x(lambda: self._db.execute("SELECT trace FROM traces WHERE ref=?", (ref,)).fetchone())
        return json.loads(row[0]) if row else None

    def put_aux(self, kind, key, record):
        return self._x(lambda: self._db.execute(
            "INSERT OR IGNORE INTO aux(kind,key,record) VALUES(?,?,?)", (kind, key, json.dumps(record, ensure_ascii=False))).rowcount == 1)

    def get_aux(self, kind, key):
        row = self._x(lambda: self._db.execute("SELECT record FROM aux WHERE kind=? AND key=?", (kind, key)).fetchone())
        return json.loads(row[0]) if row else None

    def list_aux(self, kind):
        return self._x(lambda: [json.loads(r) for (r,) in self._db.execute("SELECT record FROM aux WHERE kind=? ORDER BY key", (kind,))])

    def ping(self):
        self._x(lambda: self._db.execute("SELECT 1").fetchone())

    def close(self) -> None:
        self._db.close()


class PostgresFailureBackend(PgBase, _Backend):
    WHAT = "실패 사례 저장소"
    DDL = """
        CREATE TABLE IF NOT EXISTS cases(
          seq BIGSERIAL PRIMARY KEY, case_id TEXT NOT NULL, version INTEGER NOT NULL,
          review_status TEXT NOT NULL, failure_type TEXT NOT NULL, source_release_id TEXT NOT NULL,
          created_at TEXT NOT NULL, record TEXT NOT NULL, UNIQUE(case_id, version));
        CREATE INDEX IF NOT EXISTS cases_filter ON cases(review_status, failure_type, source_release_id);
        CREATE INDEX IF NOT EXISTS cases_release ON cases(source_release_id, created_at);
        CREATE TABLE IF NOT EXISTS traces(ref TEXT PRIMARY KEY, trace TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS aux(kind TEXT NOT NULL, key TEXT NOT NULL, record TEXT NOT NULL, PRIMARY KEY(kind, key));
    """

    def insert_case(self, record):
        return self._x(lambda: self._db.execute(
            "WITH ins AS (INSERT INTO cases(case_id,version,review_status,failure_type,source_release_id,created_at,record)"
            " VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(case_id,version) DO NOTHING RETURNING seq)"
            " SELECT seq FROM ins UNION ALL SELECT seq FROM cases WHERE case_id=%s AND version=%s LIMIT 1",
            (record["case_id"], record["version"], record["review_status"], record["failure_type"],
             record["source_release_id"], record["created_at"], json.dumps(record, ensure_ascii=False),
             record["case_id"], record["version"])).fetchone()[0])   # 끊김 뒤 재시도해도 같은 seq를 낸다

    def versions(self, case_id):
        return self._x(lambda: [json.loads(r) for (r,) in self._db.execute(
            "SELECT record FROM cases WHERE case_id=%s ORDER BY version", (case_id,)).fetchall()])

    def scan(self, after_seq):
        rows = self._x(lambda: self._db.execute("SELECT seq,record FROM cases WHERE seq>%s ORDER BY seq", (after_seq,)).fetchall())
        for s, r in rows:
            yield s, json.loads(r)

    def query(self, after_seq, latest_only, review_status, failure_type, source_release_id, since, limit):
        where, args = ["c.seq>%s"], [after_seq]
        if latest_only:
            where.append("NOT EXISTS (SELECT 1 FROM cases d WHERE d.case_id=c.case_id AND d.version>c.version)")
        for col, v in (("review_status", review_status), ("failure_type", failure_type), ("source_release_id", source_release_id)):
            if v:
                where.append(f"c.{col}=%s")
                args.append(v)
        if since:
            where.append("c.created_at>=%s")
            args.append(since)
        q = f"SELECT c.seq,c.record FROM cases c WHERE {' AND '.join(where)} ORDER BY c.seq LIMIT %s"
        rows = self._x(lambda: self._db.execute(q, (*args, limit)).fetchall())
        return [(s_, json.loads(r)) for s_, r in rows]

    def put_trace(self, ref, trace):
        self._x(lambda: self._db.execute("INSERT INTO traces(ref,trace) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                                         (ref, json.dumps(trace, ensure_ascii=False))))

    def get_trace(self, ref):
        row = self._x(lambda: self._db.execute("SELECT trace FROM traces WHERE ref=%s", (ref,)).fetchone())
        return json.loads(row[0]) if row else None

    def put_aux(self, kind, key, record):
        return self._x(lambda: self._db.execute(
            "INSERT INTO aux(kind,key,record) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
            (kind, key, json.dumps(record, ensure_ascii=False))).rowcount == 1)

    def get_aux(self, kind, key):
        row = self._x(lambda: self._db.execute("SELECT record FROM aux WHERE kind=%s AND key=%s", (kind, key)).fetchone())
        return json.loads(row[0]) if row else None

    def list_aux(self, kind):
        return self._x(lambda: [json.loads(r) for (r,) in self._db.execute(
            "SELECT record FROM aux WHERE kind=%s ORDER BY key", (kind,)).fetchall()])


def _same(a: dict, b: dict) -> bool:
    return sha256_hash(a) == sha256_hash(b)


class FailureStoreImpl:
    """I7 FailureStore. 쓰기는 이 영역(job, 검토 큐)만 한다."""

    def __init__(self, backend: _Backend) -> None:
        self._b = backend
        self._write_lock = threading.Lock()

    @classmethod
    def memory(cls) -> "FailureStoreImpl":
        return cls(MemoryBackend())

    @classmethod
    def sqlite(cls, path: str | Path) -> "FailureStoreImpl":
        return cls(SqliteBackend(path))

    @classmethod
    def postgres(cls, url: str) -> "FailureStoreImpl":
        return cls(PostgresFailureBackend(url))

    def backend_kind(self) -> str:
        return getattr(self._b, "KIND", "sqlite" if isinstance(self._b, SqliteBackend) else "memory")

    # --- 쓰기 (이 영역 내부) ---
    def put_trace(self, ref: str, trace: dict) -> None:
        """비식별 TurnTrace 저장. ref는 내용 해시에서 만들어지므로 같은 ref는 같은 내용이다."""
        existing = self._b.get_trace(ref)
        if existing is not None and not _same(existing, trace):
            raise BoundaryError("CONFLICT", "같은 trace ref에 다른 내용")
        self._b.put_trace(ref, trace)

    # --- I7 ---
    def publish_case(self, case: dict) -> dict:
        validate_record("FailureCase", case)
        with self._write_lock:
            vs = self._b.versions(case["case_id"])
            for existing in vs:
                if existing["version"] == case["version"]:
                    if _same(existing, case):
                        return existing
                    raise BoundaryError("CONFLICT", f"{case['case_id']} v{case['version']}에 다른 내용")
            latest = vs[-1]["version"] if vs else 0
            if case["version"] != latest + 1:
                raise BoundaryError("FAILED_PRECONDITION", f"다음 version은 {latest + 1}이어야 한다(받은 값 {case['version']})")
            if self._b.get_trace(case["deidentified_trace_ref"]) is None:
                raise BoundaryError("FAILED_PRECONDITION", "deidentified_trace_ref에 해당하는 비식별 trace가 먼저 저장되어야 한다")
            self._b.insert_case(case)
            # 프로세스가 둘 이상이면(검토자 둘, 서버와 잡) 위의 확인과 쓰기 사이에 다른 쪽이 먼저 쓸 수 있다.
            # 쓰기는 먼저 쓴 쪽을 남기므로, 실제로 저장된 내용이 내 것이 아니면 성공으로 말하지 않고 CONFLICT를 낸다.
            for stored in self._b.versions(case["case_id"]):
                if stored["version"] == case["version"]:
                    if _same(stored, case):
                        return case
                    raise BoundaryError("CONFLICT", f"{case['case_id']} v{case['version']}에 다른 내용")
            raise BoundaryError("UNAVAILABLE", "게시한 사례를 다시 읽지 못했다")

    def get_case(self, case_id: str, *, version: Optional[int] = None) -> dict:
        vs = self._b.versions(case_id)
        if not vs:
            raise BoundaryError("NOT_FOUND", f"사례 없음: {case_id}")
        if version is None:
            return vs[-1]
        for r in vs:
            if r["version"] == version:
                return r
        raise BoundaryError("NOT_FOUND", f"사례 버전 없음: {case_id} v{version}")

    def list_cases(self, *, review_status: Optional[str] = None, failure_type: Optional[str] = None,
                   source_release_id: Optional[str] = None, updated_since: Optional[datetime] = None,
                   latest_only: bool = True, cursor: Optional[str] = None, limit: int = 100) -> tuple[list[dict], Optional[str]]:
        if limit < 1:
            raise BoundaryError("INVALID_RECORD", "limit은 1 이상")
        try:
            after = int(cursor) if cursor else 0
        except ValueError:
            raise BoundaryError("INVALID_RECORD", "cursor 형식 오류") from None
        since = fmt_ts(updated_since) if updated_since is not None else None
        rows = self._b.query(after, latest_only, review_status, failure_type, source_release_id, since, limit + 1)
        more = len(rows) > limit
        rows = rows[:limit]
        items = [r for _, r in rows]
        last_seq = rows[-1][0] if rows else None
        return items, (str(last_seq) if more and last_seq is not None else None)

    def get_trace(self, deidentified_trace_ref: str) -> dict:
        t = self._b.get_trace(deidentified_trace_ref)
        if t is None:
            raise BoundaryError("NOT_FOUND", f"비식별 trace 없음: {deidentified_trace_ref}")
        return t

    # --- 내부 보조 레코드(지표 스냅샷, 롤백 요청 기록) ---
    def put_aux(self, kind: str, key: str, record: dict) -> bool:
        return self._b.put_aux(kind, key, record)

    def get_aux(self, kind: str, key: str) -> Optional[dict]:
        return self._b.get_aux(kind, key)

    def list_aux(self, kind: str) -> list[dict]:
        return self._b.list_aux(kind)

    def ping(self) -> None:
        self._b.ping()

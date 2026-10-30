"""스키마는 시작 때 DDL을 직접 실행하지 않고 버전 마이그레이션(yoyo-migrations)으로 만든다(ADR 0021).
이미 예전 DDL로 만들어진 DB에서 시작해도 데이터를 건드리지 않고 마이그레이션 기록만 남겨야 한다."""
import sqlite3

import pytest

from conftest import envelope, make_trace, base_config
from ops_feedback.components.failure_case_store import FailureCaseStore as FailureStoreImpl
from ops_feedback.wiring.log_store import LogStore

# 단위 3까지 시작 때 실행하던 DDL 그대로(기존 운영 DB의 모양)
OLD_LOG_DDL = """
CREATE TABLE IF NOT EXISTS raw_traces(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL, turn_id TEXT NOT NULL, release_id TEXT NOT NULL,
  received_at TEXT NOT NULL, envelope TEXT NOT NULL, envelope_hash TEXT NOT NULL,
  status TEXT, config_hash TEXT, processed_at TEXT,
  UNIQUE(session_id, turn_id));
CREATE INDEX IF NOT EXISTS raw_status ON raw_traces(status, seq);
CREATE INDEX IF NOT EXISTS raw_release ON raw_traces(release_id, received_at);
"""
OLD_FAILURE_DDL = """
CREATE TABLE IF NOT EXISTS cases(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL, version INTEGER NOT NULL,
  review_status TEXT NOT NULL, failure_type TEXT NOT NULL, source_release_id TEXT NOT NULL,
  created_at TEXT NOT NULL, record TEXT NOT NULL, UNIQUE(case_id, version));
CREATE INDEX IF NOT EXISTS cases_filter ON cases(review_status, failure_type, source_release_id);
CREATE INDEX IF NOT EXISTS cases_release ON cases(source_release_id, created_at);
CREATE TABLE IF NOT EXISTS traces(ref TEXT PRIMARY KEY, trace TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS aux(kind TEXT NOT NULL, key TEXT NOT NULL, record TEXT NOT NULL, PRIMARY KEY(kind, key));
"""


def applied(path, table):
    with sqlite3.connect(path) as c:
        return [r[0] for r in c.execute(f"SELECT migration_id FROM {table} ORDER BY migration_id")]


def test_fresh_log_db_records_the_migration(tmp_path):
    p = tmp_path / "log.db"
    LogStore.sqlite(p).send(envelope(make_trace("s", "t")))
    assert applied(p, "_yoyo_migration") == ["log-0001.initial"]


def test_fresh_failure_db_records_the_migration(tmp_path):
    p = tmp_path / "f.db"
    FailureStoreImpl.sqlite(p)
    assert applied(p, "_yoyo_migration") == ["failure-0001.initial"]


def test_reopening_applies_nothing_twice(tmp_path):
    p = tmp_path / "log.db"
    for _ in range(3):
        LogStore.sqlite(p)
    assert applied(p, "_yoyo_migration") == ["log-0001.initial"]


def test_existing_log_db_from_before_migrations_is_accepted_with_its_data(tmp_path):
    p = tmp_path / "log.db"
    with sqlite3.connect(p) as c:
        c.executescript(OLD_LOG_DDL)
        c.execute("INSERT INTO raw_traces(session_id,turn_id,release_id,received_at,envelope,envelope_hash) VALUES('s','t','r','2026-10-01T00:00:00Z','{}','h')")
    store = LogStore.sqlite(p)                                    # 마이그레이션 기록이 없는 기존 DB에서 시작
    assert applied(p, "_yoyo_migration") == ["log-0001.initial"]
    with sqlite3.connect(p) as c:
        assert c.execute("SELECT COUNT(*) FROM raw_traces").fetchone()[0] == 1
    assert store.send(envelope(make_trace("s2", "t2")))["status"] == "ACCEPTED"


def test_existing_failure_db_from_before_migrations_is_accepted(tmp_path):
    p = tmp_path / "f.db"
    with sqlite3.connect(p) as c:
        c.executescript(OLD_FAILURE_DDL)
        c.execute("INSERT INTO aux(kind,key,record) VALUES('metric','k','{}')")
    FailureStoreImpl.sqlite(p)
    assert applied(p, "_yoyo_migration") == ["failure-0001.initial"]
    with sqlite3.connect(p) as c:
        assert c.execute("SELECT COUNT(*) FROM aux").fetchone()[0] == 1


def test_postgres_fresh_and_existing_schema(request):
    from conftest import _pg_schema_url
    import psycopg
    url = _pg_schema_url(request)
    for _ in range(2):                                            # 두 번째는 이미 적용된 DB
        LogStore.postgres(url)._raw._backend.close()
        FailureStoreImpl.postgres(url)._b.close()
    with psycopg.connect(url, autocommit=True) as c:
        assert sorted(r[0] for r in c.execute("SELECT migration_id FROM _yoyo_migration")) == ["failure-0001.initial", "log-0001.initial"]
        c.execute("DROP TABLE _yoyo_migration, _yoyo_log, _yoyo_version, yoyo_lock")   # 기록만 없는 기존 DB를 흉내
    LogStore.postgres(url)._raw._backend.close()                       # 표는 이미 있다: 마이그레이션이 그대로 받아들여야 한다
    FailureStoreImpl.postgres(url)._b.close()

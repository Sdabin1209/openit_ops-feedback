"""DB 계정 분리(L08, ADR 0031): 소유자는 마이그레이션(DDL)만, 앱은 DML만. 서버·잡은 OPS_DB_SCHEMA=verify만 허용한다."""
from __future__ import annotations

import os

import pytest
from boundary_contracts.errors import BoundaryError

from conftest import _pg_schema_url, envelope, make_trace
from ops_feedback.admin import __main__ as admin_cli
from ops_feedback.api import __main__ as api_main
from ops_feedback.components.failure_case_store import FailureCaseStore
from ops_feedback.job import __main__ as job_main
from ops_feedback.wiring.log_store import LogStore
from ops_feedback.wiring.storage import require_verify


def test_require_verify_defaults_to_verify_and_refuses_apply():
    assert require_verify({})["OPS_DB_SCHEMA"] == "verify"
    assert require_verify({"OPS_DB_SCHEMA": " verify "})["OPS_DB_SCHEMA"] == "verify"
    with pytest.raises(BoundaryError) as e:
        require_verify({"OPS_DB_SCHEMA": "apply"})
    assert e.value.code == "INVALID_RECORD" and "migrate" in e.value.message


def test_server_refuses_to_start_with_apply(monkeypatch, tmp_path):
    for k in [k for k in os.environ if k.startswith("OPS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("OPS_INGEST_TOKEN", "tok")
    monkeypatch.setenv("OPS_LOG_DB", str(tmp_path / "l.db"))
    monkeypatch.setenv("OPS_DB_SCHEMA", "apply")
    assert api_main.main() == 2          # 저장소를 열기 전에 거부한다(서버를 띄우지 않고 끝난다)


def test_job_refuses_to_start_with_apply(monkeypatch, capsys, tmp_path):
    called = []
    monkeypatch.setattr(job_main, "load_config", lambda p: {})
    monkeypatch.setattr(job_main, "build_from_config", lambda cfg, env: called.append(env) or (_ for _ in ()).throw(AssertionError))
    monkeypatch.setenv("OPS_DB_SCHEMA", "apply")
    assert job_main.main(["--config", "x"]) == 2
    assert "verify" in capsys.readouterr().err and not called


def _roles(request):
    """소유자(시험 DB 접속 계정)가 스키마를 만들고, DML만 가진 앱 계정과 두 URL을 돌려준다."""
    import psycopg
    owner_url = _pg_schema_url(request)
    base = os.environ["TEST_DATABASE_URL"]
    schema = owner_url.split("search_path%3D")[1]
    role = "ops_app_" + schema[-8:]
    u = psycopg.conninfo.conninfo_to_dict(base)
    app_url = f"postgresql://{role}:pw-app-1@{u['host']}:{u['port']}/{u['dbname']}?options=-csearch_path%3D{schema}"
    return owner_url, app_url, base, schema, role


def _grant_dml(c, schema, role):
    c.execute(f"GRANT USAGE ON SCHEMA {schema} TO {role}")
    c.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {schema} TO {role}")
    c.execute(f"GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA {schema} TO {role}")


def test_owner_migrates_app_does_dml_only_and_ddl_fails(request, monkeypatch):
    import psycopg
    owner_url, app_url, base, schema, role = _roles(request)
    with psycopg.connect(base, autocommit=True) as c:
        c.execute(f"CREATE ROLE {role} LOGIN PASSWORD 'pw-app-1'")
    try:
        # 1) 앱 계정은 스키마가 없으면(표가 없으면) 기동을 거부한다. 스스로 만들지 못한다.
        with psycopg.connect(base, autocommit=True) as c:
            _grant_dml(c, schema, role)
        env = require_verify({})
        with pytest.raises(BoundaryError) as e:
            LogStore.postgres(app_url, environ=env)
        assert e.value.code == "FAILED_PRECONDITION"
        # 2) 마이그레이션 잡만 소유자 계정으로 만든다(admin migrate: 두 저장소 모두)
        for k in [k for k in os.environ if k.startswith("OPS_")]:
            monkeypatch.delenv(k)
        monkeypatch.setenv("OPS_LOG_DATABASE_URL", owner_url)
        monkeypatch.setenv("OPS_FAILURE_DATABASE_URL", owner_url)
        assert admin_cli.main(["migrate"]) == 0
        with psycopg.connect(base, autocommit=True) as c:
            _grant_dml(c, schema, role)
        # 3) 앱 계정(verify)은 기동하고 DML이 된다
        app = LogStore.postgres(app_url, environ=env)
        FailureCaseStore.postgres(app_url, environ=env)._b.close()
        assert app.send(envelope(make_trace("s-r", "t-r")))["status"] == "ACCEPTED"
        # 4) 같은 계정으로 DDL은 실패한다
        db = app._raw._backend._db
        for ddl in ("CREATE TABLE should_fail(x int)", "ALTER TABLE raw_traces ADD COLUMN x int", "DROP TABLE raw_traces"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                db.execute(ddl)
        app._raw._backend.close()
    finally:
        with psycopg.connect(base, autocommit=True) as c:
            c.execute(f"DROP OWNED BY {role}")
            c.execute(f"DROP ROLE {role}")


def test_verify_refuses_when_schema_is_not_latest(request):
    import psycopg
    url = _pg_schema_url(request)
    LogStore.postgres(url)._raw._backend.close()                       # 소유자: 최신으로 만든다
    LogStore.postgres(url, environ=require_verify({}))._raw._backend.close()      # 최신이면 verify가 통과한다
    with psycopg.connect(url, autocommit=True) as c:                    # 마이그레이션 하나가 적용되지 않은 DB를 흉내 낸다
        c.execute("DELETE FROM _yoyo_migration WHERE migration_id = 'log-0001.initial'")
    with pytest.raises(BoundaryError) as e:
        LogStore.postgres(url, environ=require_verify({}))
    assert e.value.code == "FAILED_PRECONDITION" and "최신이 아니다" in e.value.message and "migrate" in e.value.message

"""스키마 마이그레이션(yoyo-migrations, ADR 0021). 시작 때 직접 DDL을 실행하지 않고 버전 있는 마이그레이션 파일을 적용한다.

파일: `migrations/<log|failure>/<sqlite|postgresql>/<log|failure>-NNNN.이름.sql`. 기록 표는 yoyo 기본(`_yoyo_migration`) 하나이고 마이그레이션 ID에 저장소 이름을
접두한다(`log-0001.initial`, `failure-0001.initial`). 시험처럼 두 저장소가 한 DB를 쓰는 구성에서도 서로 가리지 않는다. 저장소마다 표 이름을 따로 주는 방법은
쓰지 않는다: yoyo의 내부 표(`_yoyo_log` 등)는 DB당 하나라서 둘째 저장소가 DuplicateTable로 실패했다(실측).
첫 마이그레이션은 예전 DDL과 같은 IF NOT EXISTS라서, 마이그레이션 기록이 없는 기존 DB에서도 데이터를 건드리지 않고 기록만 남긴다.
동시에 떠도 yoyo의 잠금 표(`yoyo_lock`)가 한 번에 하나만 적용하게 한다(이전의 advisory lock을 대신한다).
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from boundary_contracts.errors import BoundaryError
from yoyo import get_backend, read_migrations

_DIR = Path(__file__).parent / "migrations"


def _pg_uri(url: str, connect_secs: float, ddl_secs: float) -> str:
    """접속 문자열(URL 또는 key=value)을 yoyo의 postgresql+psycopg URI로 바꾼다. 접속 상한과 문장 상한(DDL용)을 붙인다. 이 문자열은 출력하지 않는다."""
    import psycopg
    p = psycopg.conninfo.conninfo_to_dict(url)
    opts = (p.pop("options", "") + f" -c statement_timeout={max(1, int(ddl_secs * 1000))}").strip()
    user, pw = p.pop("user", None), p.pop("password", None)
    host, port, db = p.pop("host", None), p.pop("port", None), p.pop("dbname", None)
    auth = quote(user, safe="") + (":" + quote(pw, safe="") if pw is not None else "") + "@" if user else ""
    q = {**p, "options": opts, "connect_timeout": str(max(1, int(connect_secs + 0.999)))}
    return (f"postgresql+psycopg://{auth}{host or ''}{':' + str(port) if port else ''}/{db or ''}?"
            + "&".join(f"{quote(k)}={quote(str(v), safe='')}" for k, v in q.items()))


def migrate_sqlite(scope: str, path: str | Path) -> None:
    if str(path) == ":memory:":
        raise BoundaryError("INVALID_RECORD", "SQLite 메모리 DB에는 마이그레이션을 적용하지 않는다(메모리 구현을 쓴다)")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # 10초: 같은 SQLite 파일을 동시에 여는 서버와 잡이 yoyo 잠금을 기다리는 대기 상한(마이그레이션은 표 몇 개라 수 ms). 근거: ADR 0021, 측정하지 않은 보수 값.
    _apply(f"sqlite:///{os.path.abspath(path)}", scope, "sqlite", lock_secs=10.0)


def migrate_postgres(scope: str, url: str, connect_secs: float, ddl_secs: float) -> None:
    _apply(_pg_uri(url, connect_secs, ddl_secs), scope, "postgresql", lock_secs=ddl_secs)


def _apply(uri: str, scope: str, kind: str, lock_secs: float) -> None:
    migrations = read_migrations(str(_DIR / scope / kind))
    try:
        backend = get_backend(uri)
        with backend.lock(timeout=lock_secs):
            backend.apply_migrations(backend.to_apply(migrations))
    except BoundaryError:
        raise
    except Exception as e:          # 접속 문자열이나 SQL 조각이 메시지에 섞일 수 있어 클래스 이름만 남긴다
        raise BoundaryError("UNAVAILABLE", f"스키마 마이그레이션 실패({scope}/{kind}): {type(e).__name__}") from None

# 0021. 스키마는 yoyo-migrations의 버전 마이그레이션으로 만든다

- 상태: 채택(헤드 결정 D111). [0001](0001-sqlite-dev-postgres-prod.md)의 "시작 때 `CREATE TABLE IF NOT EXISTS`" 부분을 대체한다. 표준성 점검 A8의 후속.
- 맥락: 시작 때 직접 실행하는 `CREATE ... IF NOT EXISTS`는 스키마 버전 표도 ALTER 경로도 없어, 표를 바꾸는 순간 기존 DB를 올릴 방법이 없었다. 동시 기동 보호도 직접 만든 PostgreSQL advisory lock(키 7264001)이었다.
- 결정: 마이그레이션 파일을 `src/ops_feedback/migrations/<log|failure>/<sqlite|postgresql>/<저장소>-NNNN.이름.sql`에 두고 yoyo-migrations 9.0.0이 적용한다(`migrate.py`). 로그 저장소(log.db)와 실패 사례 저장소(failures.db)가 각자 자기 묶음을 적용한다. 직접 만든 DDL 문자열, `executescript`, advisory lock, `ddl()` 함수를 지웠다. `OPS_DB_SCHEMA=verify`(앱 계정에 DDL 권한이 없는 구성)와 `admin migrate`는 그대로다.
- 왜: 버전 있는 마이그레이션 도구가 이 문제의 표준 접근이다(Alembic, Flyway 계열). yoyo는 SQL 파일 그대로 쓰고 SQLite·PostgreSQL을 둘 다 지원하며 잠금 표로 동시 적용을 직렬화한다. 이 영역의 스키마는 표 몇 개라 ORM이 필요 없다(Alembic은 SQLAlchemy 의존).
- 기존 DB: 첫 마이그레이션(`*-0001.initial.sql`)은 이전 DDL과 글자까지 같은 `IF NOT EXISTS`다. 마이그레이션 기록이 없는 기존 운영 DB에서 시작하면 데이터를 건드리지 않고 기록만 남긴다(시험: `tests/test_migrations.py`, SQLite는 예전 DDL로 만든 DB와 데이터, PostgreSQL은 기록 표를 지운 DB로 확인).
- 값: 적용 잠금 대기 = `OPS_DB_DDL_TIMEOUT`(PostgreSQL, 기본 30초, ADR 0008의 값 그대로, 측정 안 함)이고 접속 상한은 `OPS_DB_CONNECT_TIMEOUT`, DDL 문장 상한은 `statement_timeout`을 같은 DDL 상한으로 준다. SQLite 잠금 대기는 yoyo 기본 10초(출처: yoyo 기본값, 측정 안 함).
- 설계 결정: 기록 표는 yoyo 기본 `_yoyo_migration` 하나이고 ID에 저장소 이름을 접두한다. 저장소마다 표 이름을 따로 주는 방법은 yoyo의 내부 표(`_yoyo_log` 등)가 DB당 하나라서 한 DB를 두 저장소가 쓰는 구성(시험)에서 `DuplicateTable`로 실패했다(실측).
- 동작 변화: SQLite `:memory:` 경로로는 저장소를 열 수 없다(yoyo가 별도 연결을 열어 같은 메모리 DB를 못 본다). 메모리가 필요하면 `LogStore.memory()`, `FailureStoreImpl.memory()`를 쓴다(이 경로를 쓰는 코드·시험은 없었다).
- 검토한 대안: Alembic(SQLAlchemy 의존, 과함), 직접 만든 버전 표(지금 문제를 다시 만든다), 현상 유지.
- 결과: 얻는 것은 스키마 변경 이력과 기존 DB 승급 경로, 직접 만든 잠금 코드 제거. 감수하는 것은 의존 4개 추가(yoyo-migrations 9.0.0, sqlparse, tabulate, importlib_metadata와 zipp, 모두 `requirements.lock`에 고정)와 yoyo가 만드는 내부 표 4개.
- 되돌리는 조건: 스키마가 커져 ORM이나 자동 생성 마이그레이션이 필요하면 Alembic으로.

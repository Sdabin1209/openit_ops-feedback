# 0031. DB 계정을 소유자(DDL)와 앱(DML)으로 나누고, 서버·잡은 스키마 검증만 한다

- 상태: 채택(L08, 헤드 지시). [0021](0021-yoyo-migrations.md)의 "시작 때 마이그레이션을 적용한다" 중 서버·잡 부분을 대체한다(yoyo 사용 자체는 유지).
- 맥락: 서버와 잡이 시작 때 `apply`(기본)로 DDL을 실행하려면 앱 계정이 표를 만들고 바꿀 수 있어야 한다. 이 DB에는 개인정보 경계인 원본 로그가 있다. 앱의 결함이나 SQL 주입이 있으면 표 삭제·변경까지 가능하다.
- 결정:
  1. PostgreSQL 계정 둘. 소유자: 스키마와 표를 소유하고 DDL을 한다(마이그레이션 잡 전용). 앱: 스키마 USAGE, 표 SELECT/INSERT/UPDATE/DELETE, 시퀀스 USAGE/SELECT만 가진다(서버와 잡이 쓴다).
  2. 서버와 잡은 `OPS_DB_SCHEMA=verify`만 허용한다. 값이 없으면 verify이고, `apply`는 시작을 거부한다(`wiring/storage.py require_verify`). 스키마가 최신이 아니면(표가 없거나 코드가 요구하는 마이그레이션 ID가 DB의 `_yoyo_migration`에 없으면) 기동을 거부하고 `admin migrate`를 안내한다(`wiring/pgbase.py`).
  3. 스키마 변경은 기존 `python -m ops_feedback.admin migrate` 잡에서만, 소유자 계정으로 한다. 새 환경 변수 이름은 없다. 같은 `OPS_LOG_DATABASE_URL_FILE`, `OPS_FAILURE_DATABASE_URL_FILE`를 migrate 잡에는 소유자 접속 문자열 파일로, 서버·잡에는 앱 접속 문자열 파일로 주는 것은 배포(deploy)가 정한다.
  4. 비밀은 `*_FILE`(`boundary_contracts.read_secret`)이다. 이 변경은 비밀 처리를 바꾸지 않는다.
- 왜: 최소 권한 원칙(OWASP ASVS V1 접근 통제 설계, NIST SP 800-53 AC-6). PostgreSQL 관행도 앱 역할과 스키마 소유 역할을 분리하는 것이다. 마이그레이션 도구(Flyway, Alembic 계열)는 별도 DDL 계정으로 돌리는 것이 표준 배포 방식이다. 서버가 시작 때 DDL을 못 하게 하면, 여러 인스턴스가 동시에 떠서 스키마를 두고 경쟁하는 일도 없다.
- 검토한 대안:
  - 계정 하나에 DDL까지 허용(현행 apply): 단순하지만 앱 침해가 곧 스키마 파괴다. 버린다.
  - 앱이 apply를 선택할 수 있게 두고 문서로만 verify를 권고: 규칙에 점검이 없다. 거부를 코드와 시험으로 둔다.
  - 소유자 URL을 위한 새 환경 변수(`OPS_*_OWNER_DATABASE_URL_FILE` 등): 계약에 이름을 추가해야 한다. 같은 이름에 다른 파일을 주는 것으로 충분해 만들지 않았다.
  - 마이그레이션 최신 판정을 표 존재만으로: 새 마이그레이션이 늘면 구버전 스키마로 기동한다. 요구 ID의 부분집합 검사로 대체했다.
- 결과: 얻는 것은 앱 계정에서 DDL이 불가능하다는 것(시험이 CREATE, ALTER, DROP이 InsufficientPrivilege임을 확인), 배포 순서가 migrate 먼저로 고정된다는 것. 감수하는 것은 배포가 migrate 잡을 먼저 돌려야 하고, 앱 계정에 새 표 권한을 migrate 뒤에 주어야 한다는 것(`ALTER DEFAULT PRIVILEGES`를 소유자에게 걸면 자동이다). 잘못 apply를 준 서버는 시작하지 못한다(이전에는 조용히 DDL을 했다). SQLite 구성(개발·시험)에는 영향이 없다. `OPS_DB_SCHEMA=apply`의 기본값이 verify로 바뀐다.
- 값: 새 한도·수치 없음. 검증 질의는 기존 문장 상한(`OPS_DB_STATEMENT_TIMEOUT`)을 따른다.
- 되돌리는 조건: 단일 계정으로만 운영할 수밖에 없는 호스팅이면, 그때는 migrate 잡 계정을 앱과 같게 두는 것으로 충분하다(코드 변경 없이 가능).

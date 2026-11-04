# 0035. 영역 DB 이미지 ops-feedback-db: 공식 postgres 이미지 위에 계정·권한·접속 규칙·TLS

- 상태: 채택(헤드 결정 D126 "DB 는 그 데이터 주인 영역의 컴포넌트", 계약 3.4.10~3.4.12 AreaDatabaseEnv·DatabaseDeclaration). [0031](0031-db-owner-and-app-roles.md)의 계정 분리를 이미지 안에서 만든다.
- 맥락: 지금까지 PostgreSQL 은 deploy 가 만들고 계정·권한은 문서(runbook)로만 있었다. D126 은 DB 를 데이터 주인 영역의 컴포넌트로 두고, deploy 는 선언대로 비밀을 만들어 배치만 한다.
- 결정:
  1. 이미지 `ops-feedback-db`(`scripts/build-image.sh`의 variant `db`, 소스 `db/`): 베이스 `postgres:16`(공식 이미지). 엔진·버전은 라벨 `io.openit.database`(계약 DatabaseDeclaration: postgresql, major 16, port 5432)로 선언한다. 입력은 AreaDatabaseEnv(`POSTGRES_PASSWORD_FILE`, `DB_ACCOUNT_SECRETS_DIR`, `DB_TLS_DIR`)뿐이고 이미지에 비밀이 없다.
  2. 계정(선언 `db/database.json`): 저장소마다 소유자(`ops_log_owner`, `ops_failure_owner`: DDL, `admin migrate`용)와 앱용 dml(`ops_log_app`, `ops_failure_app`: 서버·잡). 데이터베이스는 `ops_log`, `ops_failure` 둘(ADR 0002 저장소 분리). `url_env`는 OpsFeedbackServerEnv 의 `OPS_LOG_DATABASE_URL_FILE`, `OPS_FAILURE_DATABASE_URL_FILE`이다(소유자 컨테이너와 앱 컨테이너가 같은 이름에 다른 파일을 받는다, ADR 0031).
  3. 초기화(공식 이미지의 `/docker-entrypoint-initdb.d` 후크, 데이터 폴더가 비어 있을 때 한 번, `db/10-accounts.sh`): 계정마다 `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`와 접속 수 상한, 소유자는 자기 데이터베이스의 소유자. dml 은 `CONNECT`, 스키마 `USAGE`, 소유자가 만드는 표에 `SELECT/INSERT/UPDATE/DELETE`, 시퀀스 `USAGE/SELECT`를 기본 권한(`ALTER DEFAULT PRIVILEGES FOR ROLE 소유자`)으로 받는다. `postgres`·`template1`을 포함해 모든 데이터베이스에서 PUBLIC `CONNECT`를 회수하고 스키마 `public`의 PUBLIC 권한도 회수한다. 암호는 `DB_ACCOUNT_SECRETS_DIR/<계정>` 파일에서 psql 백틱으로 읽는다(명령줄 인자·출력에 없음).
  4. 접속 규칙 `db/pg_hba.conf`(서버는 `hba_file`로 직접 읽고 데이터 폴더 사본도 같은 파일로 바꾼다): trust 줄이 없다(루프백 포함). OS 사용자 postgres 의 소켓만 peer(초기화·상태 확인), 그 밖의 소켓은 reject, 네트워크는 `hostssl` + `scram-sha-256`뿐이며 TLS 없는 접속은 reject.
  5. TLS 필수: `ssl=on`, 최소 TLS 1.2, 인증서는 `DB_TLS_DIR`의 `tls.crt`·`tls.key`. 없으면 컨테이너가 시작하지 않는다. 키는 서버가 요구하는 소유·권한(0600)으로 복사해 쓴다. 클라이언트는 `sslmode=verify-full`(deploy 가 URI 를 만든다).
  6. 인증서 갱신: 진입점이 백그라운드 감시(`db/tls-watch.sh`)를 띄운다. 5초마다 인증서·키의 해시를 보고, 두 번 연속 같은 새 값이면 사본을 원자적으로 바꾸고 postmaster 에 SIGHUP 을 보낸다(PostgreSQL 은 SIGHUP 에 `ssl_cert_file`도 다시 읽는다, 공식 문서). 재시작 없이 새 인증서를 쓴다.
  7. 접속 수: 앱 계정 20, 소유자 계정 4, 서버 `max_connections=50`. 서버·잡은 저장소마다 연결 하나와 `/readyz` 확인용 짧은 연결을 쓰고 migrate 는 연결 하나다. 측정하지 않은 시작값이다.
- 왜: 계정·권한 최소화(ADR 0031, NIST SP 800-53 AC-6), 접속 규칙·scram·TLS 는 PostgreSQL 문서의 표준 구성(pg_hba.conf, `hostssl`, `scram-sha-256`), 초기화는 공식 postgres 이미지의 initdb 후크 표준이다. 선언을 이미지 안(`database.json`)에 두고 라벨로 deploy 에 알려, deploy 가 계정 이름·url_env 를 짐작하지 않는다.
- 검토한 대안: deploy 가 SQL 을 직접 실행(구조 규칙 1·6 위반: 영역 DB 의 내부를 deploy 가 안다), Alembic/별도 설정 관리(과함), 인증서를 시작 때 한 번만 읽기(갱신 때 재시작 필요, 연결이 끊긴다), inotify 감시(이미지에 도구 추가).
- 결과: 얻는 것은 영역 소유의 DB(계정·권한·TLS 가 이미지와 시험에 있음). 감수하는 것: (1) 계정 암호는 초기화 때 한 번만 적용된다 — 암호 회전은 이 이미지가 하지 않는다(deploy 가 회전하려면 새 볼륨 또는 별도 절차). (2) 선언을 바꾸면 기존 데이터 폴더에는 반영되지 않는다(initdb 후크는 한 번). (3) 이미지에 jq 가 들어간다(Debian 패키지, 버전 고정 안 함). (4) 베이스 `postgres:16`은 태그 고정이고 digest 고정이 아니다. (5) 값(접속 수, 5초 주기)은 측정하지 않았다.
- 되돌리는 조건: 스키마 이름 공간이 늘어 한 클러스터로 부족하면 데이터베이스를 나눈다. 암호 회전이 필요하면 ALTER ROLE 단계를 진입점에 더한다.

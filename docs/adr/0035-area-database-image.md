# 0035. 영역 DB 이미지 ops-feedback-db: 공식 postgres 이미지 위에 계정·권한·접속 규칙·TLS

- 상태: 채택(실행 사용자·capability 는 [0036](0036-db-image-non-root.md)이 대체. 헤드 결정 D126 "DB 는 그 데이터 주인 영역의 컴포넌트", 계약 3.4.10~3.4.12 AreaDatabaseEnv·DatabaseDeclaration). [0031](0031-db-owner-and-app-roles.md)의 계정 분리를 이미지 안에서 만든다.
- 맥락: 지금까지 PostgreSQL 은 deploy 가 만들고 계정·권한은 문서(runbook)로만 있었다. D126 은 DB 를 데이터 주인 영역의 컴포넌트로 두고, deploy 는 선언대로 비밀을 만들어 배치만 한다.
- 결정:
  1. 이미지 `ops-feedback-db`(`scripts/build-image.sh`의 variant `db`, 소스 `db/`): 베이스 `postgres:16`(공식 이미지). 엔진·버전은 라벨 `io.openit.database`(계약 DatabaseDeclaration: postgresql, major 16, port 5432)로 선언한다. 입력은 AreaDatabaseEnv(`POSTGRES_PASSWORD_FILE`, `DB_ACCOUNT_SECRETS_DIR`, `DB_TLS_DIR`)뿐이고 이미지에 비밀이 없다.
  2. 계정(선언 `db/database.json`): 저장소마다 소유자(`ops_log_owner`, `ops_failure_owner`: DDL, `admin migrate`용)와 앱용 dml(`ops_log_app`, `ops_failure_app`: 서버·잡). 데이터베이스는 `ops_log`, `ops_failure` 둘(ADR 0002 저장소 분리). `url_env`는 OpsFeedbackServerEnv 의 `OPS_LOG_DATABASE_URL_FILE`, `OPS_FAILURE_DATABASE_URL_FILE`이다(소유자 컨테이너와 앱 컨테이너가 같은 이름에 다른 파일을 받는다, ADR 0031).
  3. 초기화(공식 이미지의 `/docker-entrypoint-initdb.d` 후크, 데이터 폴더가 비어 있을 때 한 번, `db/10-accounts.sh`): 계정마다 `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`와 접속 수 상한, 소유자는 자기 데이터베이스의 소유자. dml 은 `CONNECT`, 스키마 `USAGE`, 소유자가 만드는 표에 `SELECT/INSERT/UPDATE/DELETE`, 시퀀스 `USAGE/SELECT`를 기본 권한(`ALTER DEFAULT PRIVILEGES FOR ROLE 소유자`)으로 받는다. `postgres`·`template1`을 포함해 모든 데이터베이스에서 PUBLIC `CONNECT`를 회수하고 스키마 `public`의 PUBLIC 권한도 회수한다. 암호는 `DB_ACCOUNT_SECRETS_DIR/<계정>` 파일에서 psql 백틱으로 읽는다(명령줄 인자·출력에 없음).
  4. 접속 규칙 `db/pg_hba.conf`(서버는 `hba_file`로 직접 읽고 데이터 폴더 사본도 같은 파일로 바꾼다): trust 줄이 없다(루프백 포함). OS 사용자 postgres 의 소켓만 peer(초기화·상태 확인), 그 밖의 소켓은 reject, 네트워크는 `hostssl` + `scram-sha-256`뿐이며 TLS 없는 접속은 reject.
  5. TLS 필수: `ssl=on`, 최소 TLS 1.2, 인증서는 `DB_TLS_DIR`의 `tls.crt`·`tls.key`. 없으면 컨테이너가 시작하지 않는다. 키는 서버가 요구하는 소유·권한(0600)으로 복사해 쓴다. 클라이언트는 `sslmode=verify-full`(deploy 가 URI 를 만든다).
  6. 인증서 갱신: 진입점이 백그라운드 감시(`db/tls-watch.sh`)를 띄운다. 5초마다 인증서·키의 해시를 보고, 두 번 연속 같은 새 값이면 사본을 원자적으로 바꾸고 postmaster 에 SIGHUP 을 보낸다(PostgreSQL 은 SIGHUP 에 `ssl_cert_file`도 다시 읽는다, 공식 문서). 재시작 없이 새 인증서를 쓴다.
  7. 건강 확인(Dockerfile HEALTHCHECK, Docker 표준, `db/healthcheck.sh`): 서버 인증서·키 파일이 읽히고, 선언한 모든 계정이 자기 데이터베이스에 암호로 접속되며 그 접속이 TLS 인지(`pg_stat_ssl`) 본다. deploy 가 pg_isready 로 DB 내부를 대신 알 필요가 없다(구조 규칙 1). 주기 10초, 제한 5초, 시작 유예 60초, 연속 3회 실패면 unhealthy: 측정하지 않은 시작값. 컨테이너 안에는 서버 인증서의 루트가 없어 `sslmode=require`로 보고 이름 검증은 호출자(verify-full) 몫이다.
  8. 접속 수: 앱 계정 20, 소유자 계정 4, 서버 `max_connections=50`. 서버·잡은 저장소마다 연결 하나와 `/readyz` 확인용 짧은 연결을 쓰고 migrate 는 연결 하나다. 측정하지 않은 시작값이다.
- 왜: 계정·권한 최소화(ADR 0031, NIST SP 800-53 AC-6), 접속 규칙·scram·TLS 는 PostgreSQL 문서의 표준 구성(pg_hba.conf, `hostssl`, `scram-sha-256`), 초기화는 공식 postgres 이미지의 initdb 후크 표준이다. 선언을 이미지 안(`database.json`)에 두고 라벨로 deploy 에 알려, deploy 가 계정 이름·url_env 를 짐작하지 않는다.
- 검토한 대안: deploy 가 SQL 을 직접 실행(구조 규칙 1·6 위반: 영역 DB 의 내부를 deploy 가 안다), Alembic/별도 설정 관리(과함), 인증서를 시작 때 한 번만 읽기(갱신 때 재시작 필요, 연결이 끊긴다), inotify 감시(이미지에 도구 추가).
- 결과: 얻는 것은 영역 소유의 DB(계정·권한·TLS 가 이미지와 시험에 있음). 감수하는 것: (1) 계정 암호는 초기화 때 한 번만 적용된다 — 암호 회전은 이 이미지가 하지 않는다(deploy 가 회전하려면 새 볼륨 또는 별도 절차). (2) 선언을 바꾸면 기존 데이터 폴더에는 반영되지 않는다(initdb 후크는 한 번). (3) 이미지에 jq 가 들어간다(Debian 패키지, 버전 고정 안 함). (4) 베이스 `postgres:16`은 태그 고정이고 digest 고정이 아니다. (5) 값(접속 수, 5초 주기)은 측정하지 않았다.
- 되돌리는 조건: 스키마 이름 공간이 늘어 한 클러스터로 부족하면 데이터베이스를 나눈다. 암호 회전이 필요하면 ALTER ROLE 단계를 진입점에 더한다.


## 2026-11-06 배포 권한 회귀 수정

배치 비밀(폴더 0750·파일 0640)은 바꾸지 않는다. 공식 진입점의 gosu가 postgres 보조 그룹을 재설정하므로 root 단계에서 선언된 계정 파일을 /run/ops-feedback-db-secrets(postgres 소유 0700, 파일 0600)에 준비한다. 읽기 실패와 빈 첫 줄은 initdb 전에 거부한다. psql 외부 명령 실패도 SHELL_ERROR로 확인하고 명시적인 SQL 예외로 중단한다. TLS 감시자는 gosu postgres kill로 같은 사용자에게 신호를 보내며 CAP_KILL을 추가하지 않는다. 기존 AreaDatabaseEnv 입력만 사용한다.

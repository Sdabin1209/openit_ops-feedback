#!/bin/bash
# 공식 postgres 이미지의 initdb 후크(/docker-entrypoint-initdb.d, 데이터 폴더가 비어 있을 때 한 번): 선언(database.json)대로 계정·데이터베이스·권한을 만든다(ADR 0035).
# 암호는 DB_ACCOUNT_SECRETS_DIR/<계정 이름> 파일에서 읽고 어떤 출력에도 쓰지 않는다(D84). 오류는 즉시 중단한다.
set -euo pipefail
DECL=/etc/ops-feedback-db/database.json
SECRETS=/run/ops-feedback-db-secrets   # 진입점이 배치 비밀을 postgres 전용 0600 사본으로 준비한다
# 접속 수 상한: 앱 계정 20, 소유자 계정 4. 서버·잡은 저장소마다 연결 하나 + /readyz 확인용 짧은 연결이고 migrate 는 한 연결이다. 측정하지 않은 시작값(ADR 0035).
LIMIT_DML=20
LIMIT_OWNER=4

su_psql() { psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --no-password --no-psqlrc "$@"; }

# 계정: LOGIN 만(슈퍼유저·DB 생성·역할 생성·복제 없음). 암호는 psql 변수로 넘겨 SQL 문자열을 직접 만들지 않는다.
while IFS=$'\t' read -r name priv; do
  [ -s "$SECRETS/$name" ] || { echo "계정 $name 의 암호 파일이 없거나 비어 있다" >&2; exit 1; }
  limit=$LIMIT_DML; [ "$priv" = owner ] && limit=$LIMIT_OWNER
  # 암호는 명령줄 인자가 아니라 psql 의 백틱으로 파일에서 읽는다(프로세스 목록에 보이지 않게)
  PW_FILE="$SECRETS/$name" su_psql --dbname postgres -v name="$name" -v lim="$limit" <<'SQL'
\set pw `head -n1 "$PW_FILE"`
-- psql 의 외부 명령 실패는 ON_ERROR_STOP 대상이 아니므로 명시적으로 SQL 오류로 바꾼다.
\if :SHELL_ERROR
DO $$ BEGIN RAISE EXCEPTION 'account secret could not be read'; END $$;
\endif
SELECT length(:'pw') > 0 AS password_present \gset
\if :password_present
\else
DO $$ BEGIN RAISE EXCEPTION 'account secret must not be empty'; END $$;
\endif
CREATE ROLE :"name" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT :lim PASSWORD :'pw';
SQL
done < <(jq -r '.accounts[] | [.name, .privilege] | @tsv' "$DECL")

# 기본 데이터베이스는 PUBLIC 접속을 없앤다(선언한 계정만 자기 데이터베이스에 접속한다).
for d in postgres template1; do su_psql --dbname postgres -v d="$d" <<'SQL'
REVOKE ALL ON DATABASE :"d" FROM PUBLIC;
SQL
done

# 데이터베이스: 소유자 계정이 소유한다(데이터베이스마다 소유자 정확히 하나). 스키마 변경은 소유자만, dml 계정은 표 읽기·쓰기와 시퀀스 사용만.
for db in $(jq -r '[.accounts[].database] | unique[]' "$DECL"); do
  owner=$(jq -r --arg d "$db" '[.accounts[] | select(.database==$d and .privilege=="owner") | .name] | if length==1 then .[0] else error("owner must be exactly one") end' "$DECL")
  su_psql --dbname postgres -v db="$db" -v owner="$owner" <<'SQL'
CREATE DATABASE :"db" OWNER :"owner";
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
SQL
  su_psql --dbname "$db" -v owner="$owner" <<'SQL'
REVOKE ALL ON SCHEMA public FROM PUBLIC;
SQL
  for app in $(jq -r --arg d "$db" '.accounts[] | select(.database==$d and .privilege=="dml") | .name' "$DECL"); do
    su_psql --dbname "$db" -v db="$db" -v owner="$owner" -v app="$app" <<'SQL'
GRANT CONNECT ON DATABASE :"db" TO :"app";
GRANT USAGE ON SCHEMA public TO :"app";
-- 소유자가 앞으로 만드는 표·시퀀스(migrate)에 자동으로 dml 권한이 붙는다. 소유자가 만들지 않은 객체에는 붙지 않는다.
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app";
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO :"app";
SQL
  done
done

# 데이터 폴더의 pg_hba.conf 도 이미지의 것으로 바꾼다(initdb 가 만든 trust 줄을 남기지 않는다). 서버는 hba_file 로 같은 파일을 직접 쓴다.
cp /etc/ops-feedback-db/pg_hba.conf "$PGDATA/pg_hba.conf"

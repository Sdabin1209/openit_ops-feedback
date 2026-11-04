#!/bin/bash
# ops-feedback-db HEALTHCHECK(Docker 표준): 선언한 모든 계정이 자기 데이터베이스에 TLS 로 접속되고, 서버 인증서·키 파일이 읽히는지 본다(ADR 0035).
# 비밀 값은 출력하지 않는다(실패 이유만 한 줄). 종료 코드 0 건강, 1 아님.
set -uo pipefail
TLS=${DB_TLS_DIR:-/certs}
SECRETS=${DB_ACCOUNT_SECRETS_DIR:-/run/secrets/db}
DECL=/etc/ops-feedback-db/database.json
[ -r "$TLS/tls.crt" ] && [ -r "$TLS/tls.key" ] || { echo "서버 인증서 파일을 읽을 수 없다" >&2; exit 1; }
while IFS=$'\t' read -r name db; do
  [ -s "$SECRETS/$name" ] || { echo "계정 $name 의 암호 파일이 없다" >&2; exit 1; }
  # 왜 sslmode=require: 컨테이너 안에는 서버 인증서의 루트가 없다(이름 검증은 호출자 몫). 여기서는 TLS 로 접속되는지(pg_stat_ssl)와 계정 인증이 되는지를 본다.
  out=$(PGPASSWORD=$(head -n1 "$SECRETS/$name") PGSSLMODE=require PGCONNECT_TIMEOUT=3 psql -h localhost -U "$name" -d "$db" -w -At --no-psqlrc \
        -c "SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()" 2>/dev/null) || { echo "계정 $name 접속 실패" >&2; exit 1; }
  [ "$out" = "t" ] || { echo "계정 $name 접속이 TLS 가 아니다" >&2; exit 1; }
done < <(jq -r '.accounts[] | [.name, .database] | @tsv' "$DECL")

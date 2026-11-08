#!/bin/bash
# ops-feedback-db 진입점(ADR 0035, 0036: 처음부터 postgres 사용자(uid 999)로 실행한다, root 단계 없음): 서비스 이름 인증서(DB_TLS_DIR/tls.crt·tls.key)를 서버가 요구하는 소유·권한으로 옮기고 공식 postgres 진입점을 부른다.
# 인증서가 없으면 TLS 없이 뜨지 않고 실패한다(서버 TLS 필수). 비밀 값은 출력하지 않는다.
set -euo pipefail
TLS=${DB_TLS_DIR:-/certs}
[ -n "${POSTGRES_PASSWORD_FILE:-}" ] || { echo "POSTGRES_PASSWORD_FILE 이 필요하다" >&2; exit 1; }
[ -r "$TLS/tls.crt" ] && [ -r "$TLS/tls.key" ] || { echo "DB_TLS_DIR($TLS)에 tls.crt, tls.key 가 필요하다(서버 TLS 필수)" >&2; exit 1; }
# 왜 복사하나: PostgreSQL 은 키 파일이 서버 사용자 소유이고 0600 이어야 읽는다(공식 문서 ssl_key_file). 마운트된 비밀 파일의 소유·권한은 deploy 가 정한다.
install -d -m 0700 /run/ops-feedback-db-tls
install -m 0644 "$TLS/tls.crt" /run/ops-feedback-db-tls/server.crt
install -m 0600 "$TLS/tls.key" /run/ops-feedback-db-tls/server.key
# 왜 사본: 배치의 0640 비밀(그룹 SECRETS_GID 는 시작 프로세스의 보조 그룹)을 완화하지 않고, 초기화 스크립트와 상태 확인이 쓰는 postgres 전용 tmpfs 사본(0600)을 만든다.
SECRETS=${DB_ACCOUNT_SECRETS_DIR:-/run/secrets/db}
PRIVATE_SECRETS=/run/ops-feedback-db-secrets
install -d -m 0700 "$PRIVATE_SECRETS"
while IFS= read -r name; do
  secret="$SECRETS/$name"
  [ -r "$secret" ] && [ -s "$secret" ] || { echo "계정 $name 의 암호 파일이 없거나 비어 있다(또는 읽을 수 없다)" >&2; exit 1; }
  first_line=$(head -n1 "$secret") || { echo "계정 $name 의 암호 파일을 읽을 수 없다" >&2; exit 1; }
  [ -n "$first_line" ] || { echo "계정 $name 의 암호 첫 줄이 비어 있다" >&2; exit 1; }
  unset first_line
  install -m 0600 "$secret" "$PRIVATE_SECRETS/$name"
done < <(jq -r '.accounts[].name' /etc/ops-feedback-db/database.json)
# 인증서 갱신 감시(tls-watch.sh)는 서버와 같은 컨테이너 안의 백그라운드 프로세스다. 서버가 PID 1 이 되면 이 프로세스는 그 자식으로 남는다.
/usr/local/bin/ops-feedback-db-tls-watch.sh &
exec docker-entrypoint.sh "$@"

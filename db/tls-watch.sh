#!/bin/bash
# 서버 인증서 갱신을 스스로 다시 읽는다(ADR 0035): DB_TLS_DIR 의 tls.crt·tls.key 가 바뀌면 서버가 쓰는 사본을 갱신하고 PostgreSQL 에 SIGHUP(설정 다시 읽기, ssl_cert_file 도 다시 읽는다 - 공식 문서)을 보낸다.
# 왜 확인 주기: 인증서 갱신은 시간·일 단위 사건이라 5초면 충분하고 해시 계산 비용은 무시할 만하다(측정 안 함). 파일이 바뀐 직후 한 번 더 같은 값일 때(두 번 연속)만 적용한다: 인증서와 키가 따로 쓰이는 중간 상태를 읽지 않는다.
set -uo pipefail
TLS=${DB_TLS_DIR:-/certs}
OUT=/run/ops-feedback-db-tls
sig() { cat "$TLS/tls.crt" "$TLS/tls.key" 2>/dev/null | sha256sum | cut -d' ' -f1; }
applied=$(sig); prev=$applied
while true; do
  sleep 5
  cur=$(sig)
  if [ "$cur" = "$prev" ] && [ "$cur" != "$applied" ] && [ -r "$TLS/tls.crt" ] && [ -r "$TLS/tls.key" ]; then
    install -o postgres -g postgres -m 0644 "$TLS/tls.crt" "$OUT/server.crt.new" && install -o postgres -g postgres -m 0600 "$TLS/tls.key" "$OUT/server.key.new" \
      && mv "$OUT/server.key.new" "$OUT/server.key" && mv "$OUT/server.crt.new" "$OUT/server.crt" || continue
    pid=$(head -n1 "${PGDATA:-/var/lib/postgresql/data}/postmaster.pid" 2>/dev/null) && [ -n "$pid" ] && kill -HUP "$pid" && { applied=$cur; echo "ops-feedback-db: TLS 인증서를 다시 읽었다"; }
  fi
  prev=$cur
done

# ops-feedback-db: 이 영역의 PostgreSQL(D126: DB 는 데이터 주인 영역의 컴포넌트). 공식 postgres 이미지 위에 계정·권한·접속 규칙·TLS 설정을 얹는다(ADR 0035).
# 입력은 계약 AreaDatabaseEnv(POSTGRES_PASSWORD_FILE, DB_ACCOUNT_SECRETS_DIR, DB_TLS_DIR)뿐이다. 이미지에 비밀 값이 없다.
FROM postgres:16
ARG COMMIT=n/a
ARG CONTRACTS_VERSION=n/a
ARG DATABASE_DECLARATION
LABEL io.openit.service="ops-feedback-db" \
      io.openit.revision=$COMMIT \
      io.openit.contracts.boundary=$CONTRACTS_VERSION \
      io.openit.contracts.survey-schema="n/a" \
      io.openit.database=$DATABASE_DECLARATION
# jq: 초기화 스크립트가 선언(database.json)을 읽는다. Debian 패키지(버전 고정 안 함).
RUN apt-get update && apt-get install -y --no-install-recommends jq && rm -rf /var/lib/apt/lists/*
COPY database.json pg_hba.conf /etc/ops-feedback-db/
COPY 10-accounts.sh /docker-entrypoint-initdb.d/10-accounts.sh
COPY entrypoint.sh /usr/local/bin/ops-feedback-db-entrypoint.sh
COPY tls-watch.sh /usr/local/bin/ops-feedback-db-tls-watch.sh
RUN chmod 0755 /docker-entrypoint-initdb.d/10-accounts.sh /usr/local/bin/ops-feedback-db-entrypoint.sh /usr/local/bin/ops-feedback-db-tls-watch.sh
ENTRYPOINT ["ops-feedback-db-entrypoint.sh"]
# 서버 설정은 명령줄 -c 로 준다(데이터 폴더의 postgresql.conf 를 고치지 않는다): TLS 필수, TLS 1.2 이상, 이미지의 접속 규칙, 접속 수 상한 50(계정 상한 합 80 보다 작다: 서버·잡·migrate 가 동시에 상한까지 쓰지 않는다. 측정 안 함).
CMD ["postgres", "-c", "listen_addresses=*", "-c", "ssl=on", "-c", "ssl_cert_file=/run/ops-feedback-db-tls/server.crt", "-c", "ssl_key_file=/run/ops-feedback-db-tls/server.key", \
     "-c", "ssl_min_protocol_version=TLSv1.2", "-c", "hba_file=/etc/ops-feedback-db/pg_hba.conf", "-c", "password_encryption=scram-sha-256", "-c", "max_connections=50"]

# ops-feedback-db: 이 영역의 PostgreSQL(D126: DB 는 데이터 주인 영역의 컴포넌트). 공식 postgres 이미지 위에 계정·권한·접속 규칙·TLS 설정을 얹는다(ADR 0035).
# 입력은 계약 AreaDatabaseEnv(POSTGRES_PASSWORD_FILE, DB_ACCOUNT_SECRETS_DIR, DB_TLS_DIR)뿐이다. 이미지에 비밀 값이 없다.
# 기반 이미지는 digest 로 고정한다(재현 가능한 빌드). 값은 2026-11-05 이 PC 에서 postgres:16 의 RepoDigest, 이 이미지로 DB 이미지 시험이 통과했다. 올릴 때는 이 한 줄만 바꾸고 시험을 다시 돌린다(선언의 major 16 도 함께 본다).
ARG POSTGRES_IMAGE=postgres:16@sha256:1beaa3084eee2676f20a9cd9928f0bf2c5bf674f8e49f81017912183d377c61c
FROM ${POSTGRES_IMAGE}
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
COPY healthcheck.sh /usr/local/bin/ops-feedback-db-healthcheck.sh
RUN chmod 0755 /docker-entrypoint-initdb.d/10-accounts.sh /usr/local/bin/ops-feedback-db-entrypoint.sh /usr/local/bin/ops-feedback-db-tls-watch.sh /usr/local/bin/ops-feedback-db-healthcheck.sh
# 건강 확인: 선언한 계정으로 TLS 접속이 되는지(deploy 가 pg_isready 로 DB 내부를 대신 알 필요가 없다). 주기 10초, 제한 5초, 시작 유예 60초(첫 초기화 시간), 연속 3회 실패하면 unhealthy: Docker 표준 옵션이고 값은 측정하지 않은 시작값이다.
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=3 CMD ["ops-feedback-db-healthcheck.sh"]
# 왜 USER postgres: 루트로 뜨는 DB 컨테이너는 침해 때 호스트 쪽 권한 상승 면이 넓고, 공식 postgres 이미지는 root 가 아니어도 뜨도록 만들어져 있다(공식 문서 "Arbitrary --user Notes"). uid·gid 999 는 이 이미지의 postgres 사용자이고 inputs.json 의 process 와 같다(build-image.sh 가 대조한다, ADR 0036).
USER postgres
ENTRYPOINT ["ops-feedback-db-entrypoint.sh"]
# 서버 설정은 명령줄 -c 로 준다(데이터 폴더의 postgresql.conf 를 고치지 않는다): TLS 필수, TLS 1.2 이상, 이미지의 접속 규칙, 접속 수 상한 50(계정 상한 합 80 보다 작다: 서버·잡·migrate 가 동시에 상한까지 쓰지 않는다. 측정 안 함).
CMD ["postgres", "-c", "listen_addresses=*", "-c", "ssl=on", "-c", "ssl_cert_file=/run/ops-feedback-db-tls/server.crt", "-c", "ssl_key_file=/run/ops-feedback-db-tls/server.key", \
     "-c", "ssl_min_protocol_version=TLSv1.2", "-c", "hba_file=/etc/ops-feedback-db/pg_hba.conf", "-c", "password_encryption=scram-sha-256", "-c", "max_connections=50"]

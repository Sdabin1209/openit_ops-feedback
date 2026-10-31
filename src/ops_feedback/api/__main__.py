"""ingest-api 서버 진입점. 설정은 환경 변수로만 받는다(여기서만 읽는다): OPS_INGEST_TOKEN(수집 쓰기), OPS_LOG_DB, OPS_PORT(기본 8080),
읽기 면: OPS_FAILURE_DB와 OPS_READ_TOKENS=역할=토큰,역할=토큰 (둘 다 있을 때만 켠다). 검토 면: OPS_REVIEW_TOKENS=역할=토큰,…(읽기 면과 같이 켜진다).
비밀은 NAME_FILE로 받는다(D84). 로그는 표준 출력으로 낸다. 업무 규칙 없음: 경로·인증·검증은 components/link_ingest_api.py(통신 기반 라이브러리 위)가 한다."""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading

from boundary_contracts import read_secret
from boundary_contracts.errors import BoundaryError
from boundary_contracts.secrets import SecretError

from ..components.link_ingest_api import READYZ_CACHE_SECONDS, make_server
from ..config import JsonFormatter, parse_read_tokens, validate_server_env
from ..wiring.check_settings import server_settings
from ..wiring.ingest_service import IngestService
from ..wiring.storage import open_failure_store, open_log_store

log = logging.getLogger("ingest-api")


def main() -> int:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    try:
        validate_server_env(os.environ)        # 모르는 OPS_ 변수는 시작을 거부한다(OpsFeedbackServerEnv)
    except ValueError as e:
        log.error("%s", e)
        return 2
    try:
        token = read_secret("OPS_INGEST_TOKEN") or ""
        read_raw = read_secret("OPS_READ_TOKENS")
        review_raw = read_secret("OPS_REVIEW_TOKENS")
    except SecretError as e:
        log.error("%s", e)
        return 2
    if not token:
        log.error("OPS_INGEST_TOKEN_FILE 또는 OPS_INGEST_TOKEN이 필요하다")
        return 2
    try:
        store = open_log_store(os.environ, os.environ.get("OPS_LOG_DB", "/data/log.db"))
        failures, rtoks = None, {}
        if read_raw:
            failures = open_failure_store(os.environ, os.environ.get("OPS_FAILURE_DB"))
            if failures is not None:
                rtoks = parse_read_tokens(read_raw)
    except (BoundaryError, SecretError) as e:
        log.error("저장소를 열지 못했다: %s", getattr(e, "message", None) or type(e).__name__)
        return 2
    vtoks = parse_read_tokens(review_raw or "")
    # 원본 보관 기간은 잡 설정(retention.raw_days)이 정한다. 서버는 그 값을 알 수 없으므로 배포가 같은 값을 OPS_RAW_RETENTION_DAYS로 선언해야 경고가 사라진다(선언일 뿐 서버가 지우지 않는다).
    declared = (os.environ.get("OPS_RAW_RETENTION_DAYS") or "").strip() != ""
    require_user = os.environ.get("OPS_REVIEW_REQUIRE_USER", "1").strip().lower() not in ("0", "false", "no")
    try:
        st = server_settings(os.environ, tokens=[token, *rtoks, *vtoks], retention_declared=declared, require_reviewer_identity=require_user,
                             default_cache=READYZ_CACHE_SECONDS)       # 범위(연결 수 1 이상, 캐시 0 이상 등)와 토큰 서로 다름은 ServerSettings 스키마가 정한다
    except ValueError as e:
        log.error("%s", e)
        return 2
    if not declared:
        log.warning("보관 기간 미설정: 원본이 무기한 보관된다(RAW_RETENTION_UNSET). 보관 기간은 사용자 결정이며, 정해지면 잡 retention.raw_days와 OPS_RAW_RETENTION_DAYS에 같은 값을 둔다")
    if not require_user:
        log.warning("검토자 신원 필수가 꺼져 있다(REVIEW_IDENTITY_NOT_REQUIRED): 검토 게시가 개인 식별 없이 기록될 수 있다")
    srv = make_server(IngestService(store, failures), token, port=st["port"], read_tokens=rtoks, review_tokens=vtoks,
                      max_connections=st["max_connections"], readyz_cache_seconds=st["readyz_cache_seconds"], retention_declared=declared,
                      require_reviewer_identity=require_user)
    # SIGTERM(docker stop)에 새 연결을 멈추고 끝낸다. 쓰기는 자동 커밋 단일 문장이라 처리 중이던 요청은 완결되거나 호출자가 재시도한다.
    # 왜 "listening on"보다 먼저 등록하나: 그 로그 줄을 기동 신호로 삼는 쪽(시험·운영 도구)이 곧바로 SIGTERM을 보내도 핸들러가 이미 있어야 한다.
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=srv.shutdown, daemon=True).start())
    log.info("listening on %s", srv.server_address[1])
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

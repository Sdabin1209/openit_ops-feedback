"""ingest-api: TraceEnvelope 수집 HTTP 서비스(I6). 표준 라이브러리만 쓴다.

  POST /v1/traces   본문 TraceEnvelope(JSON), Authorization: Bearer <토큰>  -> 200 IngestResult | 오류 BoundaryError 레코드
  GET  /healthz     살아 있음
  GET  /readyz      저장소가 열려 있고 스키마가 적재됨

설정은 환경 변수로만 받는다(진입점에서만 읽는다). OPS_INGEST_TOKEN, OPS_LOG_DB, OPS_PORT(기본 8080).
로그는 표준 출력으로 낸다. 본문(개인정보 가능)은 로그에 남기지 않는다.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from boundary_contracts.errors import BoundaryError

from .logstore import LogStore
from .schemas import supported_trace_versions

log = logging.getLogger("ingest-api")
HTTP_STATUS = {"NOT_FOUND": 404, "INVALID_RECORD": 400, "UNSUPPORTED_VERSION": 422, "CONFLICT": 409,
               "INTEGRITY_ERROR": 422, "FAILED_PRECONDITION": 412, "PERMISSION_DENIED": 403, "UNAVAILABLE": 503}
MAX_BODY = 8 * 1024 * 1024


def make_server(store: LogStore, token: str, host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:
    if not token:
        raise ValueError("인증 토큰이 비어 있다")

    class H(BaseHTTPRequestHandler):
        server_version = "ops-feedback-ingest"

        def log_message(self, fmt, *args):      # 요청 줄만 표준 출력으로
            log.info("%s %s", self.command, self.path.split("?")[0])

        def _json(self, code: int, body: dict) -> None:
            raw = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _err(self, e: BoundaryError) -> None:
            self._json(HTTP_STATUS[e.code], e.to_record())

        def do_GET(self):
            if self.path == "/healthz":
                return self._json(200, {"status": "ok"})
            if self.path == "/readyz":
                try:
                    store.ping()
                    if not supported_trace_versions():
                        raise BoundaryError("UNAVAILABLE", "문진 스키마가 없다")
                except BoundaryError as e:
                    return self._json(503, {"status": "not-ready", "code": e.code})
                return self._json(200, {"status": "ready", "supported_runtime_contract_versions": supported_trace_versions()})
            self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/v1/traces":
                return self._json(404, {"error": "not found"})
            auth = self.headers.get("Authorization", "")
            if not (auth.startswith("Bearer ") and hmac.compare_digest(auth[7:], token)):
                return self._err(BoundaryError("PERMISSION_DENIED", "토큰이 맞지 않는다"))
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = -1
            if n <= 0 or n > MAX_BODY:
                return self._err(BoundaryError("INVALID_RECORD", "본문 크기가 맞지 않는다"))
            try:
                env = json.loads(self.rfile.read(n))
            except (ValueError, UnicodeDecodeError):
                return self._err(BoundaryError("INVALID_RECORD", "JSON이 아니다"))
            try:
                self._json(200, store.send(env))
            except BoundaryError as e:
                self._err(e)
            except Exception:                    # 예상 밖 오류도 서비스를 죽이지 않는다
                log.exception("수집 중 예외")
                self._err(BoundaryError("UNAVAILABLE", "내부 오류"))

    return ThreadingHTTPServer((host, port), H)


def main() -> int:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format='{"ts":"%(asctime)s","lvl":"%(levelname)s","msg":"%(message)s"}')
    token = os.environ.get("OPS_INGEST_TOKEN", "")
    if not token:
        log.error("OPS_INGEST_TOKEN이 필요하다")
        return 2
    store = LogStore.sqlite(os.environ.get("OPS_LOG_DB", "/data/log.db"))
    srv = make_server(store, token, port=int(os.environ.get("OPS_PORT", "8080")))
    log.info("listening on %s", srv.server_address[1])
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

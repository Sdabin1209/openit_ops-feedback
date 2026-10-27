"""ingest-api: TraceEnvelope 수집 HTTP 서비스(I6). 표준 라이브러리만 쓴다.

  POST /v1/traces   본문 TraceEnvelope(JSON), Authorization: Bearer <토큰>  -> 200 IngestResult | 오류 BoundaryError 레코드
  GET  /v1/cases/{case_id}?version=                  I7 get_case (읽기 토큰)
  GET  /v1/cases?review_status=&failure_type=&source_release_id=&updated_since=&latest_only=&cursor=&limit=   I7 list_cases
  GET  /v1/deidentified-traces/{ref}                  I7 get_trace (비식별 뒤의 것만 저장되어 있다)
  POST /v1/cases/{case_id}/review  {review_status, expected?, expected_kind?, reviewed_input?}  검토 게시(검토 토큰, 역할 reviewer; 헤더 X-Openit-User가 있으면 그 값을 ext["ops-feedback"].reviewed_by에, 없으면 역할 이름). 201 새 version / 200 같은 검토 재전송 / 404
  GET  /healthz     살아 있음
  GET  /readyz      저장소가 열려 있고 스키마가 적재됨

설정은 환경 변수로만 받는다(진입점에서만 읽는다). OPS_INGEST_TOKEN(수집 쓰기), OPS_LOG_DB, OPS_PORT(기본 8080),
읽기 면: OPS_FAILURE_DB와 OPS_READ_TOKENS=역할=토큰,역할=토큰 (둘 다 있을 때만 켠다). 수집 토큰과 읽기 토큰은 서로의 경로를 호출할 수 없다(403).
검토 면: OPS_REVIEW_TOKENS=역할=토큰,…(읽기 면과 같이 켜진다). 세 종류 토큰은 서로의 경로를 호출할 수 없다.
publish_case는 HTTP로 열지 않는다(내부 잡).
로그는 표준 출력으로 낸다. 본문(개인정보 가능)은 로그에 남기지 않는다.
"""
from __future__ import annotations

from boundary_contracts import read_secret
from boundary_contracts.secrets import SecretError
import hmac
import json
import logging
import os
import re
import sys
from urllib.parse import parse_qs, unquote, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from boundary_contracts.errors import BoundaryError

from .clock import parse_ts
from .failurestore import FailureStoreImpl
from .logstore import LogStore
from .review import ReviewQueue
from .schemas import validate_record
from .schemas import supported_trace_versions

log = logging.getLogger("ingest-api")
HTTP_STATUS = {"NOT_FOUND": 404, "INVALID_RECORD": 400, "UNSUPPORTED_VERSION": 422, "CONFLICT": 409,
               "INTEGRITY_ERROR": 422, "FAILED_PRECONDITION": 412, "PERMISSION_DENIED": 403, "UNAVAILABLE": 503}
MAX_BODY = 8 * 1024 * 1024
MAX_LIMIT = 500
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def parse_read_tokens(spec: str) -> dict[str, str]:
    """'역할=토큰,역할=토큰' -> {토큰: 역할}"""
    out: dict[str, str] = {}
    for part in filter(None, (x.strip() for x in spec.split(","))):
        role, sep, tok = part.partition("=")
        if not sep or not role or not tok:
            raise ValueError("OPS_READ_TOKENS 형식은 역할=토큰,역할=토큰")
        out[tok] = role
    return out


def make_server(store: LogStore, token: str, host: str = "0.0.0.0", port: int = 8080, *,
                failures: Optional[FailureStoreImpl] = None, read_tokens: Optional[dict[str, str]] = None,
                review_tokens: Optional[dict[str, str]] = None) -> ThreadingHTTPServer:
    if not token:
        raise ValueError("인증 토큰이 비어 있다")
    read_tokens = read_tokens or {}
    review_tokens = review_tokens or {}
    if token in read_tokens or token in review_tokens or set(read_tokens) & set(review_tokens):
        raise ValueError("수집·읽기·검토 토큰은 서로 달라야 한다")
    queue = ReviewQueue(failures) if failures is not None else None

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

        def _read_auth(self) -> None:
            auth = self.headers.get("Authorization", "")
            ok = auth.startswith("Bearer ") and any(hmac.compare_digest(auth[7:], t) for t in read_tokens)
            if not ok:
                raise BoundaryError("PERMISSION_DENIED", "읽기 토큰이 맞지 않는다")

        def _read_api(self, path: str, q: dict) -> None:
            try:
                self._read_auth()
                if failures is None:
                    raise BoundaryError("NOT_FOUND", "읽기 면이 꺼져 있다")
                if path.startswith("/v1/cases/"):
                    ver = q.get("version", [None])[0]
                    try:
                        v = int(ver) if ver is not None else None
                    except ValueError:
                        raise BoundaryError("INVALID_RECORD", "version은 정수") from None
                    rec = failures.get_case(unquote(path[len("/v1/cases/"):]), version=v)
                    validate_record("FailureCase", rec)
                    return self._json(200, rec)
                if path == "/v1/cases":
                    def one(k):
                        return q.get(k, [None])[0] or None
                    try:
                        limit = int(one("limit") or 100)
                    except ValueError:
                        raise BoundaryError("INVALID_RECORD", "limit은 정수") from None
                    if not 1 <= limit <= MAX_LIMIT:
                        raise BoundaryError("INVALID_RECORD", f"limit은 1~{MAX_LIMIT}")
                    since = None
                    if one("updated_since"):
                        try:
                            since = parse_ts(one("updated_since").replace(" ", "+"))
                        except ValueError:
                            raise BoundaryError("INVALID_RECORD", "updated_since는 RFC 3339") from None
                    lo = (one("latest_only") or "true").lower()
                    if lo not in ("true", "false"):
                        raise BoundaryError("INVALID_RECORD", "latest_only는 true|false")
                    items, nxt = failures.list_cases(review_status=one("review_status"), failure_type=one("failure_type"),
                                                     source_release_id=one("source_release_id"), updated_since=since,
                                                     latest_only=lo == "true", cursor=one("cursor"), limit=limit)
                    for it in items:
                        validate_record("FailureCase", it)
                    return self._json(200, {"items": items, "next_cursor": nxt})
                if path.startswith("/v1/deidentified-traces/"):
                    return self._json(200, failures.get_trace(unquote(path[len("/v1/deidentified-traces/"):])))
                raise BoundaryError("NOT_FOUND", "경로 없음")
            except BoundaryError as e:
                self._err(e)

        def do_GET(self):
            u = urlsplit(self.path)
            if u.path.startswith("/v1/"):
                return self._read_api(u.path, parse_qs(u.query))
            if self.path == "/healthz":
                return self._json(200, {"status": "ok"})
            if self.path == "/readyz":
                try:
                    store.ping()
                    if failures is not None:
                        failures.ping()
                    if not supported_trace_versions():
                        raise BoundaryError("UNAVAILABLE", "문진 스키마가 없다")
                except BoundaryError as e:
                    return self._json(503, {"status": "not-ready", "code": e.code})
                return self._json(200, {"status": "ready", "supported_runtime_contract_versions": supported_trace_versions()})
            self._json(404, {"error": "not found"})

        def _review(self, path: str) -> None:
            try:
                auth = self.headers.get("Authorization", "")
                role = next((r for t, r in review_tokens.items() if auth.startswith("Bearer ") and hmac.compare_digest(auth[7:], t)), None)
                if role is None:
                    raise BoundaryError("PERMISSION_DENIED", "검토 토큰이 맞지 않는다")
                if queue is None:
                    raise BoundaryError("NOT_FOUND", "검토 면이 꺼져 있다")
                case_id = unquote(path[len("/v1/cases/"):-len("/review")])
                try:
                    n = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    n = -1
                if n <= 0 or n > MAX_BODY:
                    raise BoundaryError("INVALID_RECORD", "본문 크기가 맞지 않는다")
                try:
                    body = json.loads(self.rfile.read(n))
                except (ValueError, UnicodeDecodeError):
                    raise BoundaryError("INVALID_RECORD", "JSON이 아니다") from None
                if not isinstance(body, dict) or set(body) - {"review_status", "expected", "expected_kind", "reviewed_input"} or "review_status" not in body:
                    raise BoundaryError("INVALID_RECORD", "본문은 {review_status, expected?, expected_kind?}")
                exp = body.get("expected")
                if exp is not None and not isinstance(exp, (dict, str)):
                    raise BoundaryError("INVALID_RECORD", "expected는 객체(LORA_A) 또는 문자열(LORA_B)")
                ri = body.get("reviewed_input")
                if ri is not None and not isinstance(ri, dict):
                    raise BoundaryError("INVALID_RECORD", "reviewed_input은 객체")
                if exp is None and ri is None and body.get("expected_kind") is not None:
                    raise BoundaryError("INVALID_RECORD", "expected_kind는 expected 또는 reviewed_input과 함께만 쓴다")
                user = self.headers.get("X-Openit-User")
                if user is not None and not _ID.fullmatch(user):
                    raise BoundaryError("INVALID_RECORD", "X-Openit-User는 Id 형식([A-Za-z0-9][A-Za-z0-9._:-]*, 128자 이하)이어야 한다")
                before = failures.get_case(case_id)                       # 없으면 NOT_FOUND(404)
                rec = queue.review(case_id, body["review_status"], role, expected=exp, expected_kind=body.get("expected_kind"), reviewed_input=ri, reviewed_by=user)
                validate_record("FailureCase", rec)
                self._json(201 if rec["version"] > before["version"] else 200, rec)
            except BoundaryError as e:
                self._err(e)
            except Exception:
                log.exception("검토 중 예외")
                self._err(BoundaryError("UNAVAILABLE", "내부 오류"))

        def do_POST(self):
            u = urlsplit(self.path)
            if u.path.startswith("/v1/cases/") and u.path.endswith("/review"):
                return self._review(u.path)
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
    store = LogStore.sqlite(os.environ.get("OPS_LOG_DB", "/data/log.db"))
    failures, rtoks = None, {}
    if os.environ.get("OPS_FAILURE_DB") and read_raw:
        failures = FailureStoreImpl.sqlite(os.environ["OPS_FAILURE_DB"])
        rtoks = parse_read_tokens(read_raw)
    vtoks = parse_read_tokens(review_raw or "")
    srv = make_server(store, token, port=int(os.environ.get("OPS_PORT", "8080")), failures=failures, read_tokens=rtoks, review_tokens=vtoks)
    log.info("listening on %s", srv.server_address[1])
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

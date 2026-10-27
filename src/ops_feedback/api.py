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
import signal
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from boundary_contracts.errors import BoundaryError

from .clock import parse_ts
from .failurestore import FailureStoreImpl
from .logstore import LogStore
from .review import ReviewQueue
from .storage import open_failure_store, open_log_store
from .schemas import validate_record
from .schemas import supported_trace_versions

log = logging.getLogger("ingest-api")
HTTP_STATUS = {"NOT_FOUND": 404, "INVALID_RECORD": 400, "UNSUPPORTED_VERSION": 422, "CONFLICT": 409,
               "INTEGRITY_ERROR": 422, "FAILED_PRECONDITION": 412, "PERMISSION_DENIED": 403, "UNAVAILABLE": 503}
MAX_BODY = 8 * 1024 * 1024
MAX_REVIEW_BODY = 1024 * 1024     # 검토 게시 본문: 검토자가 쓰는 짧은 JSON이라 수집(8 MiB)보다 작게 둔다. 임의 상한, 측정 안 함(ADR 0008)
MAX_LIMIT = 500
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def _eq(a: str, b: str) -> bool:
    """상수 시간 비교. 비ASCII 문자가 든 헤더도 TypeError 없이 거부한다."""
    return hmac.compare_digest(a.encode("utf-8", "surrogateescape"), b.encode("utf-8", "surrogateescape"))


class JsonFormatter(logging.Formatter):
    """한 줄 JSON. 메시지의 따옴표·개행을 이스케이프해 요청 경로로 로그 줄을 위조할 수 없다."""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({"ts": self.formatTime(record), "lvl": record.levelname, "msg": record.getMessage()}, ensure_ascii=False)


class BoundedServer(ThreadingHTTPServer):
    """동시 연결 상한이 있는 서버. 상한을 넘는 연결은 스레드를 만들지 않고 바로 503으로 닫는다."""
    daemon_threads = True

    def __init__(self, addr, handler, max_connections: int = 64):
        super().__init__(addr, handler)
        self._slots = threading.BoundedSemaphore(max_connections)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nRetry-After: 1\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


REQUEST_TIMEOUT = 30.0        # 근거: 임의 보수값, 측정 안 함(ADR 0008).        # 소켓 읽기·쓰기 상한(초). 본문을 덜 보내고 멈춘 연결이 스레드를 영구 점유하지 못하게 한다
READYZ_CACHE_SECONDS = 1.5    # 근거: probe 상한 2초(FAULT_POLICY 5)보다 짧게 두어 장애 감지를 크게 늦추지 않으면서 반복 호출을 흡수하는 임의값, 측정 안 함(ADR 0008).    # 인증 없는 /readyz가 호출마다 DB 연결을 열지 못하게 결과를 짧게 공유한다(0이면 끈다)


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
                review_tokens: Optional[dict[str, str]] = None, max_connections: int = 64,
                readyz_cache_seconds: float = READYZ_CACHE_SECONDS, retention_declared: bool = True,
                require_reviewer_identity: bool = True) -> ThreadingHTTPServer:
    if not token:
        raise ValueError("인증 토큰이 비어 있다")
    read_tokens = read_tokens or {}
    review_tokens = review_tokens or {}
    if token in read_tokens or token in review_tokens or set(read_tokens) & set(review_tokens):
        raise ValueError("수집·읽기·검토 토큰은 서로 달라야 한다")
    queue = ReviewQueue(failures) if failures is not None else None
    counters = {"auth_rejected": 0, "ingest_accepted": 0, "ingest_duplicate": 0, "unavailable": 0}   # 프로세스 안 누적(재시작하면 0). 인증 실패는 조용히 사라지지 않는다(FAULT_POLICY 9)
    ready_cache: dict = {"at": 0.0, "val": None}
    ready_lock = threading.Lock()

    def check_ready() -> Optional[str]:
        """저장소 확인. 실패하면 오류 코드, 정상이면 None. 결과를 readyz_cache_seconds 동안 공유한다(동시 호출도 한 번만 확인)."""
        with ready_lock:
            now = time.monotonic()
            if ready_cache["val"] is not None and readyz_cache_seconds > 0 and now - ready_cache["at"] < readyz_cache_seconds:
                return ready_cache["val"][0]
            checks = [store.ping] + ([failures.ping] if failures is not None else [])
            errs: list = []

            def run(fn):
                try:
                    fn()
                except BoundaryError as e:
                    errs.append(e)
            ts = [threading.Thread(target=run, args=(c,), daemon=True) for c in checks]    # 두 저장소는 동시에: 각 확인이 상한(기본 2초)을 가진다
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            code = errs[0].code if errs else (None if supported_trace_versions() else "UNAVAILABLE")
            ready_cache.update(at=time.monotonic(), val=(code,))
            return code

    class H(BaseHTTPRequestHandler):
        server_version = "ops-feedback-ingest"
        timeout = REQUEST_TIMEOUT

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
            if e.code == "PERMISSION_DENIED":
                counters["auth_rejected"] += 1
            elif e.code == "UNAVAILABLE":
                counters["unavailable"] += 1
            if e.code in ("PERMISSION_DENIED", "UNAVAILABLE"):     # 접근 로그에는 상태 코드가 없다. 인증 실패(토큰 회전·오설정)와 저장소 장애가 로그에서 보이게 한다(값·토큰 없이 경로와 코드만).
                log.warning("거절 %s %s -> %s", self.command, self.path.split("?")[0], e.code)
            self._json(HTTP_STATUS[e.code], e.to_record())

        def _read_auth(self) -> None:
            auth = self.headers.get("Authorization", "")
            ok = auth.startswith("Bearer ") and any(_eq(auth[7:], t) for t in read_tokens)
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
            except Exception:                    # 예상 밖 오류도 연결을 끊지 않고 503 본문으로 알린다
                log.exception("읽기 중 예외")
                self._err(BoundaryError("UNAVAILABLE", "내부 오류"))

        def do_GET(self):
            u = urlsplit(self.path)
            if u.path.startswith("/v1/"):
                return self._read_api(u.path, parse_qs(u.query))
            if self.path == "/healthz":
                return self._json(200, {"status": "ok"})
            if self.path == "/readyz":
                code = check_ready()
                if code is not None:
                    return self._json(503, {"status": "not-ready", "code": code})
                body = {"status": "ready", "log_store": store.backend_kind(),
                        "failure_store": failures.backend_kind() if failures is not None else None,
                        "supported_runtime_contract_versions": supported_trace_versions(), "counters": dict(counters)}
                warns = []
                if not retention_declared:
                    warns.append("RAW_RETENTION_UNSET")             # 원본 보관 기간이 정해져 있지 않다(무기한 보관). 보관 기간은 사용자 결정이다
                if not require_reviewer_identity:
                    warns.append("REVIEW_IDENTITY_NOT_REQUIRED")    # OPS_REVIEW_REQUIRE_USER=0: 검토 게시가 개인 식별 없이 들어올 수 있다(D88/U7 기본은 필수)
                if warns:
                    body["warnings"] = warns
                return self._json(200, body)
            self._json(404, {"error": "not found"})

        def _review(self, path: str) -> None:
            try:
                auth = self.headers.get("Authorization", "")
                role = next((r for t, r in review_tokens.items() if auth.startswith("Bearer ") and _eq(auth[7:], t)), None)
                if role is None:
                    raise BoundaryError("PERMISSION_DENIED", "검토 토큰이 맞지 않는다")
                if queue is None:
                    raise BoundaryError("NOT_FOUND", "검토 면이 꺼져 있다")
                case_id = unquote(path[len("/v1/cases/"):-len("/review")])
                try:
                    n = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    n = -1
                if n <= 0 or n > MAX_REVIEW_BODY:
                    raise BoundaryError("INVALID_RECORD", "본문 크기가 맞지 않는다")
                try:
                    body = json.loads(self.rfile.read(n))
                except (ValueError, UnicodeDecodeError, RecursionError):
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
                if user is None and require_reviewer_identity:
                    # D88/U7: 검토 게시는 gateway가 인증한 사용자(X-Openit-User)가 있어야 받는다. 없으면 토큰 역할만으로는 누가 검토했는지 남지 않는다.
                    raise BoundaryError("PERMISSION_DENIED", "검토자 신원(X-Openit-User)이 필요하다")
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
            if not (auth.startswith("Bearer ") and _eq(auth[7:], token)):
                return self._err(BoundaryError("PERMISSION_DENIED", "토큰이 맞지 않는다"))
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = -1
            if n <= 0 or n > MAX_BODY:
                return self._err(BoundaryError("INVALID_RECORD", "본문 크기가 맞지 않는다"))
            try:
                env = json.loads(self.rfile.read(n))
            except (ValueError, UnicodeDecodeError, RecursionError):
                return self._err(BoundaryError("INVALID_RECORD", "JSON이 아니다"))
            try:
                res = store.send(env)
                counters["ingest_accepted" if res.get("status") == "ACCEPTED" else "ingest_duplicate"] += 1
                self._json(200, res)
            except BoundaryError as e:
                self._err(e)
            except Exception:                    # 예상 밖 오류도 서비스를 죽이지 않는다
                log.exception("수집 중 예외")
                self._err(BoundaryError("UNAVAILABLE", "내부 오류"))

    return BoundedServer((host, port), H, max_connections)


def main() -> int:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
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
    try:
        max_conn = int(os.environ.get("OPS_MAX_CONNECTIONS", "64"))
        cache_s = float(os.environ.get("OPS_READYZ_CACHE_SECONDS", str(READYZ_CACHE_SECONDS)))
        if max_conn < 1 or cache_s < 0:
            raise ValueError
    except ValueError:
        log.error("OPS_MAX_CONNECTIONS는 1 이상의 정수, OPS_READYZ_CACHE_SECONDS는 0 이상의 숫자")
        return 2
    # 원본 보관 기간은 잡 설정(retention.raw_days)이 정한다. 서버는 그 값을 알 수 없으므로 배포가 같은 값을 OPS_RAW_RETENTION_DAYS로 선언해야 경고가 사라진다(선언일 뿐 서버가 지우지 않는다).
    declared = (os.environ.get("OPS_RAW_RETENTION_DAYS") or "").strip() != ""
    if not declared:
        log.warning("보관 기간 미설정: 원본이 무기한 보관된다(RAW_RETENTION_UNSET). 보관 기간은 사용자 결정이며, 정해지면 잡 retention.raw_days와 OPS_RAW_RETENTION_DAYS에 같은 값을 둔다")
    if os.environ.get("OPS_REVIEW_REQUIRE_USER", "1").strip().lower() in ("0", "false", "no"):
        log.warning("검토자 신원 필수가 꺼져 있다(REVIEW_IDENTITY_NOT_REQUIRED): 검토 게시가 개인 식별 없이 기록될 수 있다")
    srv = make_server(store, token, port=int(os.environ.get("OPS_PORT", "8080")), failures=failures, read_tokens=rtoks, review_tokens=vtoks,
                      max_connections=max_conn, readyz_cache_seconds=cache_s, retention_declared=declared,
                      require_reviewer_identity=os.environ.get("OPS_REVIEW_REQUIRE_USER", "1").strip().lower() not in ("0", "false", "no"))
    log.info("listening on %s", srv.server_address[1])
    # SIGTERM(docker stop)에 새 연결을 멈추고 끝낸다. 쓰기는 자동 커밋 단일 문장이라 처리 중이던 요청은 완결되거나 호출자가 재시도한다.
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=srv.shutdown, daemon=True).start())
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

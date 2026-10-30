"""ingest-api: TraceEnvelope 수집 HTTP 서비스(I6)와 실패 사례 읽기(I7)·검토 게시 면. 받는 쪽 통신 컴포넌트(D118): openit_link.App/Route 위에 경로만 적는다.

  POST /v1/traces   본문 TraceEnvelope(JSON), Authorization: Bearer <수집 토큰>  -> 200 IngestResult | 오류 BoundaryError 레코드
  GET  /v1/cases/{case_id}?version=                  I7 get_case (읽기 토큰)
  GET  /v1/cases?review_status=&failure_type=&source_release_id=&updated_since=&latest_only=&cursor=&limit=   I7 list_cases
  GET  /v1/deidentified-traces/{ref}                  I7 get_trace (비식별 뒤의 것만 저장되어 있다)
  POST /v1/cases/{case_id}/review  {review_status, expected?, expected_kind?, reviewed_input?}  검토 게시(검토 토큰; X-Openit-User가 검토자, 없으면 필수 설정에 따라 401). 201 새 version / 200 같은 검토 재전송 / 404
  GET  /healthz     살아 있음            GET  /readyz   저장소가 열려 있고 스키마가 적재됨
401/403, X-Openit-User, 413, 요청·응답 계약 검증, BoundaryError 본문, /healthz는 openit_link가 한다(여기서 다시 구현하지 않는다). 이 파일의 몫: 경로 표, 토큰 -> 역할 이름,
업무 서비스(wiring의 IngestService)로의 연결, 쿼리 문자열 검증(ListCasesQuery), /readyz 본문, 프로세스 안 계수(인증 거절은 응답 상태를 보고 센다).
publish_case는 HTTP로 열지 않는다(내부 잡). 본문(개인정보 가능)은 로그에 남기지 않는다.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import waitress
import waitress.wasyncore
from boundary_contracts import validator
from boundary_contracts.errors import BoundaryError
from openit_link import App, Request, Response, Route

from ..wiring.validate import check

log = logging.getLogger("ingest-api")
_READYZ = validator("OpsFeedbackReadyz")
MAX_BODY = 8 * 1024 * 1024        # 수집 본문 상한: TurnTrace 봉투 하나. 임의 상한, 측정 안 함(ADR 0008)
MAX_REVIEW_BODY = 1024 * 1024     # 검토 게시 본문: 검토자가 쓰는 짧은 JSON이라 수집(8 MiB)보다 작게 둔다. 임의 상한, 측정 안 함(ADR 0008)

REQUEST_TIMEOUT = 30.0        # 근거: 임의 보수값, 측정 안 함(ADR 0008).        # 소켓 읽기·쓰기 상한(초). 본문을 덜 보내고 멈춘 연결이 스레드를 영구 점유하지 못하게 한다
SHUTDOWN_GRACE_SECONDS = 8.0   # 근거: docker stop 기본 10초(SIGKILL 전)보다 짧게 둔 제약값. 측정 안 함(ADR 0020).    # 종료 때 진행 중 요청을 기다리는 상한
SHUTDOWN_FLUSH_SECONDS = 1.0   # 근거: 임의 보수값, 측정 안 함(ADR 0020).    # 끝난 요청의 응답이 소켓으로 나가길 기다리는 상한
READYZ_CACHE_SECONDS = 1.5    # 근거: probe 상한 2초(FAULT_POLICY 5)보다 짧게 두어 장애 감지를 크게 늦추지 않으면서 반복 호출을 흡수하는 임의값, 측정 안 함(ADR 0008).    # 인증 없는 /readyz가 호출마다 DB 연결을 열지 못하게 결과를 짧게 공유한다(0이면 끈다)

INGEST, READ, REVIEW = "ingest", "read:", "review:"      # 토큰 -> 역할 이름의 접두. 면마다 이름 공간을 나눠 설정의 역할 이름과 부딪치지 않게 한다


class Server:
    """waitress 서버를 감싼 얇은 손잡이: 시험과 진입점이 `server_address`, `serve_forever()`, `shutdown()`으로 다룬다."""

    def __init__(self, app, host: str, port: int, *, connection_limit: int, channel_timeout: float) -> None:
        # connection_limit + 2: waitress는 내부 map 크기(듣는 소켓 + 깨우기 trigger + 연결)로 상한을 재서 (상한 - 2)개까지만 받는다(실측: 상한 3에서 연결 1개). 상한 그대로 받게 2를 더한다.
        # cleanup_interval: 멈춘 연결을 channel_timeout 안팎에 닫으려고 점검 주기를 줄인다(기본 30초면 timeout이 최대 두 배 늦다).
        # 왜 이 값들: ADR 0020. threads=connection_limit은 연결마다 일꾼이 있던 이전 동작(DB가 막혀도 /healthz가 대답)을 유지하려는 것이다.
        self._srv = waitress.create_server(app, host=host, port=port, threads=connection_limit, connection_limit=connection_limit + 2,
                                           channel_timeout=channel_timeout, cleanup_interval=min(5.0, channel_timeout), max_request_body_size=MAX_BODY, ident="ops-feedback-ingest",
                                           clear_untrusted_proxy_headers=False)
        self.server_address = (self._srv.effective_host, self._srv.effective_port)
        self._closing = False

    def serve_forever(self) -> None:
        try:
            self._srv.run()
        except (OSError, ValueError):
            # 종료 때 다른 스레드가 소켓을 닫으면 이 스레드의 select가 EBADF로 끝난다(waitress 단일 서버는 다른 스레드에서의 close를 안전하게 받지 않는다).
            # 종료 중일 때만 정상으로 보고, 그 밖의 오류는 그대로 올린다.
            if not self._closing:
                raise

    def shutdown(self, grace: float = SHUTDOWN_GRACE_SECONDS) -> None:
        """새 연결을 멈추고, 진행 중 요청을 마친 뒤(최대 grace초) 응답이 나가는 것을 기다려 연결을 닫는다."""
        srv = self._srv
        self._closing = True
        srv.close()                                                       # 듣는 소켓만 닫는다(waitress 단일 서버의 close)
        srv.task_dispatcher.shutdown(cancel_pending=False, timeout=grace)  # 일꾼이 지금 하던 요청을 끝내고 나간다
        deadline = time.monotonic() + SHUTDOWN_FLUSH_SECONDS
        while time.monotonic() < deadline and any(getattr(c, "total_outbufs_len", 0) or getattr(c, "requests", None) for c in list(srv._map.values())):
            time.sleep(0.02)
        waitress.wasyncore.close_all(srv._map)


def make_app(service, token: str, *, read_tokens: Optional[dict[str, str]] = None,
             review_tokens: Optional[dict[str, str]] = None, readyz_cache_seconds: float = READYZ_CACHE_SECONDS,
             retention_declared: bool = True, require_reviewer_identity: bool = True):
    """WSGI 앱(PEP 3333). service는 wiring/ingest_service.py의 IngestService가 정한 면을 가진 객체다(이 컴포넌트는 업무 컴포넌트를 모른다, 구조 규칙 3)."""
    read_tokens = read_tokens or {}
    review_tokens = review_tokens or {}
    try:
        # 토큰이 비어 있지 않고 서로 달라야 한다: ServerSettings.tokens(minLength, uniqueItems)가 정한다. 값은 오류에 싣지 않는다.
        check("ServerSettings", {"tokens": [token, *read_tokens, *review_tokens]})
    except BoundaryError:
        raise ValueError("수집·읽기·검토 토큰은 비어 있지 않고 서로 달라야 한다(ServerSettings.tokens)") from None
    tokens = {token: INGEST, **{t: READ + r for t, r in read_tokens.items()}, **{t: REVIEW + r for t, r in review_tokens.items()}}
    counters = {"auth_rejected": 0, "ingest_accepted": 0, "ingest_duplicate": 0, "unavailable": 0}   # 프로세스 안 누적(재시작하면 0). 인증 실패는 조용히 사라지지 않는다(FAULT_POLICY 9)
    ready_cache: dict = {"at": 0.0, "val": None}
    ready_lock = threading.Lock()

    def guarded(fn):
        def run(req: Request) -> Response:
            try:
                return fn(req)
            except BoundaryError:
                raise
            except Exception:                    # 예상 밖 오류도 연결을 끊지 않고 503 본문으로 알린다
                log.exception("처리 중 예외")
                raise BoundaryError("UNAVAILABLE", "내부 오류") from None
        return run

    def check_ready() -> Optional[str]:
        """저장소 확인. 실패하면 오류 코드, 정상이면 None. 결과를 readyz_cache_seconds 동안 공유한다(동시 호출도 한 번만 확인)."""
        with ready_lock:
            now = time.monotonic()
            if ready_cache["val"] is not None and readyz_cache_seconds > 0 and now - ready_cache["at"] < readyz_cache_seconds:
                return ready_cache["val"][0]
            errs: list = []

            def run(fn):
                try:
                    fn()
                except BoundaryError as e:
                    errs.append(e)
            ts = [threading.Thread(target=run, args=(c,), daemon=True) for c in service.pings()]    # 두 저장소는 동시에: 각 확인이 상한(기본 2초)을 가진다
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            code = errs[0].code if errs else (None if service.supported_versions() else "UNAVAILABLE")
            ready_cache.update(at=time.monotonic(), val=(code,))
            return code

    def ready():
        code = check_ready()
        if code is not None:
            return False, {"status": "not-ready", "code": code}
        body = {"status": "ready", "log_store": service.log_kind(), "failure_store": service.failure_kind(),
                "supported_runtime_contract_versions": service.supported_versions(), "counters": dict(counters)}
        warns = []
        if not retention_declared:
            warns.append("RAW_RETENTION_UNSET")             # 원본 보관 기간이 정해져 있지 않다(무기한 보관). 보관 기간은 사용자 결정이다
        if not require_reviewer_identity:
            warns.append("REVIEW_IDENTITY_NOT_REQUIRED")    # OPS_REVIEW_REQUIRE_USER=0: 검토 게시가 개인 식별 없이 들어올 수 있다(D88/U7 기본은 필수)
        if warns:
            body["warnings"] = warns
        bad = list(_READYZ.iter_errors(body))
        if bad:
            # 자기 검사: 본문 모양이 계약과 어긋나는 것은 코드 버그다. 응답은 막지 않고(상태 코드가 판단 근거) 오류로 남긴다.
            log.error("readyz 본문이 OpsFeedbackReadyz를 어긴다: %s", "; ".join(e.validator for e in bad[:3]))
        return True, body

    def query(req: Request) -> dict:
        """쿼리 문자열의 값들. 형식·범위는 ListCasesQuery 스키마가 정한다(limit 1~500, latest_only true|false, RFC 3339 시각, 정수 version)."""
        one = {k: v for k, v in req.query.items() if v}
        if "updated_since" in one:
            one["updated_since"] = one["updated_since"].replace(" ", "+")
        check("ListCasesQuery", one)
        return one

    def need_read() -> None:
        if not service.read_enabled:
            raise BoundaryError("NOT_FOUND", "읽기 면이 꺼져 있다")

    @guarded
    def post_trace(req: Request) -> Response:
        res = service.send(req.body)
        counters["ingest_accepted" if res.get("status") == "ACCEPTED" else "ingest_duplicate"] += 1
        return Response(200, res)

    @guarded
    def get_case(req: Request) -> Response:
        need_read()
        ver = query(req).get("version")
        return Response(200, service.get_case(req.params["case_id"], int(ver) if ver is not None else None))

    @guarded
    def list_cases(req: Request) -> Response:
        need_read()
        one = query(req)
        items, nxt = service.list_cases(review_status=one.get("review_status"), failure_type=one.get("failure_type"),
                                        source_release_id=one.get("source_release_id"), updated_since=one.get("updated_since"),
                                        latest_only=one.get("latest_only", "true") == "true", cursor=one.get("cursor"), limit=int(one.get("limit", 100)))
        for it in items:
            check("FailureCase", it, contract=True)
        return Response(200, {"items": items, "next_cursor": nxt})

    @guarded
    def get_trace(req: Request) -> Response:
        need_read()
        return Response(200, service.get_trace(req.params["ref"]))

    @guarded
    def review(req: Request) -> Response:
        check("ReviewHeaders", {} if req.user is None else {"user": req.user})       # Id 형식([A-Za-z0-9][A-Za-z0-9._:-]*, 128자 이하)
        if not service.review_enabled:
            raise BoundaryError("NOT_FOUND", "검토 면이 꺼져 있다")
        rec, created = service.review(req.params["case_id"], req.body, req.role[len(REVIEW):], req.user)
        return Response(201 if created else 200, rec)

    def roles(prefix: str) -> frozenset:
        # 빈 집합은 openit_link에서 "인증 없음"이다. 토큰이 하나도 없는 면도 인증을 요구하도록 어떤 토큰도 갖지 않는 역할을 더한다(없는 면: 토큰 틀림 401, 수집 토큰 403).
        return frozenset(r for r in tokens.values() if r.startswith(prefix)) | {prefix + "\0"}

    routes = [
        Route("POST", "/v1/traces", post_trace, roles=frozenset({INGEST}), request_schema="TraceEnvelope", response_schema="IngestResult", max_body=MAX_BODY),
        Route("GET", "/v1/cases", list_cases, roles=roles(READ)),
        Route("GET", "/v1/cases/{case_id}", get_case, roles=roles(READ), response_schema="FailureCase"),
        Route("GET", "/v1/deidentified-traces/{ref}", get_trace, roles=roles(READ)),
        Route("POST", "/v1/cases/{case_id}/review", review, roles=roles(REVIEW), request_schema="CaseReviewRequest", response_schema="FailureCase",
              require_user=require_reviewer_identity, max_body=MAX_REVIEW_BODY),
    ]
    inner = App(routes, tokens, ready=ready)

    def app(environ, start_response):
        log.info("%s %s", environ.get("REQUEST_METHOD"), environ.get("PATH_INFO"))          # 요청 줄만(본문·쿼리는 남기지 않는다)

        def sr(status, headers, *a):
            code = int(status.split()[0])
            if code in (401, 403):
                counters["auth_rejected"] += 1
            elif code == 503 and environ.get("PATH_INFO") != "/readyz":
                counters["unavailable"] += 1
            if code in (401, 403, 503):     # 접근 로그에는 상태 코드가 없다. 인증 실패(토큰 회전·오설정)와 저장소 장애가 로그에서 보이게 한다(값·토큰 없이 경로와 코드만).
                log.warning("거절 %s %s -> %s %s", environ.get("REQUEST_METHOD"), environ.get("PATH_INFO"), code, "UNAVAILABLE" if code == 503 else "PERMISSION_DENIED")
            return start_response(status, headers, *a)
        return inner(environ, sr)

    return app


def make_server(service, token: str, host: str = "0.0.0.0", port: int = 8080, *, max_connections: int = 64, **app_kw) -> Server:
    return Server(make_app(service, token, **app_kw), host, port, connection_limit=max_connections, channel_timeout=REQUEST_TIMEOUT)

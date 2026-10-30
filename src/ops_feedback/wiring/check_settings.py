"""서버 설정 값 모으기: 환경 변수 문자열을 숫자로 바꿔 ServerSettings 스키마로 검증한다(범위·형식 규칙은 스키마에 있다)."""
from __future__ import annotations

from boundary_contracts.errors import BoundaryError

from .validate import check


def server_settings(environ, *, tokens: list, retention_declared: bool, require_reviewer_identity: bool, default_cache: float) -> dict:
    try:
        st = {"port": int(environ.get("OPS_PORT", "8080")), "max_connections": int(environ.get("OPS_MAX_CONNECTIONS", "64")),
              "readyz_cache_seconds": float(environ.get("OPS_READYZ_CACHE_SECONDS", str(default_cache))), "tokens": tokens,
              "retention_declared": retention_declared, "require_reviewer_identity": require_reviewer_identity}
    except ValueError:
        raise ValueError("OPS_PORT, OPS_MAX_CONNECTIONS는 정수, OPS_READYZ_CACHE_SECONDS는 숫자여야 한다") from None
    try:
        check("ServerSettings", st)
    except BoundaryError as e:
        # 토큰 값은 싣지 않는다(경로와 규칙 종류만)
        raise ValueError("서버 설정이 ServerSettings를 어긴다(OPS_MAX_CONNECTIONS는 1 이상, OPS_READYZ_CACHE_SECONDS는 0 이상, 토큰은 비어 있지 않고 서로 달라야 한다): "
                         + "; ".join(e.details.get("errors", []))) from None
    return st

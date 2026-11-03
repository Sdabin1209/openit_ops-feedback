"""영역 밖으로 나가는 길의 통신 기반(보내는 쪽 통신 컴포넌트): 자기 영역 게이트웨이 출구와 TLS 주소 규칙만 안다. 업무 컴포넌트는 계약 클라이언트를 모른다(구조 규칙 3).
RUNTIME R1.1(loopback 밖 http 거부), R1.8(영역 밖 호출은 OPENIT_EGRESS_PROXY 의 CONNECT 터널로만)."""
from __future__ import annotations

from typing import Mapping, Optional

from boundary_contracts.clients import egress_proxy_from, require_tls_url


def egress_proxy(environ: Mapping[str, str]) -> Optional[str]:
    """환경에서 OPENIT_EGRESS_PROXY 하나만 꺼낸다(없으면 None, 형식이 틀리면 ValueError)."""
    return egress_proxy_from(environ)


def tls_url(url: str) -> None:
    """loopback 밖의 평문 http 주소는 ValueError."""
    require_tls_url(url)

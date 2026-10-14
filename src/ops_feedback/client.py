"""문진 서비스가 쓰는 I6 클라이언트(HTTP). TraceIngest Protocol을 지키며 실패는 BoundaryError로 낸다."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from boundary_contracts.errors import BoundaryError


class HttpTraceIngest:
    def __init__(self, base_url: str, token: str, *, timeout: float = 5.0) -> None:
        self._url, self._token, self._timeout = base_url.rstrip("/") + "/v1/traces", token, timeout

    def send(self, envelope: dict) -> dict:
        req = urllib.request.Request(self._url, data=json.dumps(envelope, ensure_ascii=False).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._token}"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                raise BoundaryError.from_record(json.loads(e.read())) from None
            except (ValueError, KeyError):
                raise BoundaryError("UNAVAILABLE", f"HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise BoundaryError("UNAVAILABLE", f"연결 실패: {type(e).__name__}") from None

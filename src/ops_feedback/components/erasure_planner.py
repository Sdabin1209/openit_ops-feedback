"""세션 삭제 대상 찾기. 입력 ErasureRequest(세션 ID+그 세션의 의사 식별자), 출력 ErasureTargets(비식별 trace에서 찾을 session_id 값들).
비식별 정책에 session_id 의사화가 없으면 trace에 원래 session_id가 남아 있다: 두 값을 모두 찾는다."""
from __future__ import annotations

from ..types.ErasureRequest import ErasureRequest
from ..types.ErasureTargets import ErasureTargets
from ..wiring.validate import check


def plan(request: ErasureRequest) -> ErasureTargets:
    check("ErasureRequest", request)
    out: ErasureTargets = {"trace_session_keys": sorted({request["session_pseudonym"], request["session_id"]})}
    return out

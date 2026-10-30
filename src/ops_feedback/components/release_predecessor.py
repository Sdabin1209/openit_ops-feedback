"""되돌릴 릴리스 찾기. 입력 PredecessorRequest(최신부터의 릴리스 ID 목록), 출력 PredecessorResult. 현재 바로 다음(더 오래된) 릴리스가 되돌릴 대상이다."""
from __future__ import annotations

from ..types.PredecessorRequest import PredecessorRequest
from ..types.PredecessorResult import PredecessorResult
from ..wiring.validate import check


def find_predecessor(request: PredecessorRequest) -> PredecessorResult:
    check("PredecessorRequest", request)
    ids = request["release_ids"]
    if request["current"] in ids and ids.index(request["current"]) + 1 < len(ids):
        out: PredecessorResult = {"found": True, "previous": ids[ids.index(request["current"]) + 1]}
    else:
        out = {"found": False}
    check("PredecessorResult", out)
    return out

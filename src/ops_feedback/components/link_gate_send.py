"""이웃 release-gate에 롤백 요청 보내기(I5). 보내는 쪽 통신 컴포넌트: 계약 클라이언트(boundary_contracts.sdk.GateService 모양의 객체)를 쓴다."""
from __future__ import annotations

from boundary_contracts.errors import BoundaryError

from ..wiring.validate import check


class GateLink:
    def __init__(self, gate) -> None:
        self._gate = gate

    def request_rollback(self, request: dict) -> dict:
        """요청을 보내고 영수증을 돌려준다. 영수증은 계약 RollbackReceipt여야 하고 이 요청의 것이어야 한다."""
        receipt = self._gate.request_rollback(request)
        check("RollbackReceipt", receipt, contract=True)
        if receipt.get("request_id") != request["request_id"]:       # 다른 요청의 영수증을 이 요청의 것으로 저장하지 않는다(원칙 10)
            raise BoundaryError("INTEGRITY_ERROR", "영수증의 request_id가 요청과 다르다")
        return receipt

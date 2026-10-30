"""수집 결과 응답(IngestResult)을 만든다. 입력 IngestReceiptRequest, 출력 계약 IngestResult."""
from __future__ import annotations

from boundary_contracts import PIPELINE_SCHEMA_VERSION  # 새 레코드는 계약 상수의 버전으로 쓴다(값을 코드에 적지 않는다)

from ..wiring.validate import check


def receipt(request: dict) -> dict:
    check("IngestReceiptRequest", request)
    rec = request["record"]
    received = rec["received_at"]
    if not request["is_new"]:
        received = request.get("first_received_at") or received      # 중복이면 처음 받은 시각을 돌려준다(멱등)
    out = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "session_id": rec["session_id"], "turn_id": rec["turn_id"],
           "status": "ACCEPTED" if request["is_new"] else "DUPLICATE", "received_at": received}
    check("IngestResult", out, contract=True)      # 내는 쪽이 내기 전에 검증한다(R7)
    return out

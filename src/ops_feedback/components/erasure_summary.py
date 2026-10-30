"""삭제 결과 요약. 입력 ErasureCounts, 출력 ErasureSummary. 0건이면 오류 없이 끝난 것과 구별되게 found=false와 안내를 낸다
(잘못된 salt·정책(session_id 의사화 키가 다름)·이미 지워짐을 구별할 수 없으므로)."""
from __future__ import annotations

from ..types.ErasureCounts import ErasureCounts
from ..types.ErasureSummary import ErasureSummary
from ..wiring.validate import check


def summarize(counts: ErasureCounts) -> ErasureSummary:
    check("ErasureCounts", counts)
    found = counts["raw_rows"] + counts["traces"] + counts["cases"] > 0
    out: ErasureSummary = {**counts, "found": found}
    if not found:
        out["note"] = "NOTHING_FOUND: 해당 세션의 원본도 비식별 trace도 찾지 못했다. salt와 비식별 정책(pseudonymize_keys의 session_id)을 확인한다"
    check("ErasureSummary", out)
    return out

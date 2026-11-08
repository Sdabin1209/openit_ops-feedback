"""4.x 봉투·사례 판별의 한 곳(계약 5.x: 최상위 pipeline_schema_version 의 MAJOR 를 JSON 으로 파싱해 대조한다)."""
from __future__ import annotations

import json

LEGACY_MAJOR = "4"      # 근거: 계약 5.0.0 CHANGELOG '이전': 4.x 저장 데이터는 이전하지 않고, 남아 있으면 조용히 섞이지 않고 실패해야 한다.


def is_legacy_major(text) -> bool:
    # 왜 파싱인가: LIKE 는 ext 안의 중첩 4.x 메타데이터를 오인하고 공백·직렬화에 흔들리며 `_` 가 아무 글자와 맞는다(codex C2 검토, 2026-11-09).
    try:
        top = json.loads(text)
    except (TypeError, ValueError):
        return False
    v = top.get("pipeline_schema_version") if isinstance(top, dict) else None
    return isinstance(v, str) and v.split(".", 1)[0] == LEGACY_MAJOR


def any_legacy_major(rows) -> bool:
    """rows: 레코드 TEXT 를 하나씩 내는 순회(한 번 훑는 기동 검사라 색인이 필요 없다, 측정 안 함)."""
    return any(is_legacy_major(r) for (r,) in rows)

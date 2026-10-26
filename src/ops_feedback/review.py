"""사람 검토 큐. 검토 결과는 version을 올린 새 FailureCase로 게시한다(레코드는 불변, D25)."""
from __future__ import annotations

from typing import Optional

from boundary_contracts.errors import BoundaryError

from boundary_contracts import PIPELINE_SCHEMA_VERSION

from .clock import Clock, utc_now
from .failurestore import FailureStoreImpl
from .schemas import validate_record

# 2.0.0: expected_kind는 LORA_A|LORA_B. 정답과 검토 입력은 경계 계약의 스키마로 검증한다(문진 스키마의 v1 타입이 아니다).
_OUTPUT_DEF = {"LORA_A": "ActionExtraction", "LORA_B": "ResponseText"}      # expected: 객체 | 문자열
_INPUT_DEF = {"LORA_A": "ActionExtractionInput", "LORA_B": "ResponseRequest"}
_KINDS = "LORA_A|LORA_B"


class ReviewQueue:
    def __init__(self, store: FailureStoreImpl, *, clock: Clock = utc_now) -> None:
        self._store = store
        self._clock = clock

    def pending(self, *, failure_type: Optional[str] = None, source_release_id: Optional[str] = None,
                cursor: Optional[str] = None, limit: int = 50):
        return self._store.list_cases(review_status="PENDING", failure_type=failure_type,
                                      source_release_id=source_release_id, cursor=cursor, limit=limit)

    def review(self, case_id: str, decision: str, reviewer_role: str, *, base_version: Optional[int] = None,
               failure_type: Optional[str] = None, expected: Optional[dict] = None, expected_kind: Optional[str] = None, reviewed_input: Optional[dict] = None,
               note: Optional[str] = None, reviewed_by: Optional[str] = None) -> dict:
        if decision not in ("CONFIRMED", "DISMISSED", "PENDING"):
            raise BoundaryError("INVALID_RECORD", "decision은 CONFIRMED, DISMISSED, PENDING 중 하나")
        if decision != "CONFIRMED" and (expected is not None or reviewed_input is not None):
            raise BoundaryError("INVALID_RECORD", f"{decision}에는 expected, reviewed_input을 둘 수 없다(CONFIRMED에만)")
        if expected is None and reviewed_input is None and expected_kind is not None:
            raise BoundaryError("INVALID_RECORD", "expected_kind는 expected 또는 reviewed_input과 함께만 쓴다")
        if reviewed_input is not None and expected_kind not in _INPUT_DEF:
            raise BoundaryError("INVALID_RECORD", "reviewed_input을 적을 때는 expected_kind(LORA_A|LORA_B)도 필요하다")
        cur = self._store.get_case(case_id)
        if base_version is not None and base_version != cur["version"]:
            raise BoundaryError("CONFLICT", f"최신 version은 {cur['version']}")
        if expected is not None:
            self.validate_expected(cur, expected, expected_kind)
        if reviewed_input is not None:
            self.validate_reviewed_input(cur, reviewed_input, expected_kind)
        new = {k: v for k, v in cur.items() if k != "ext"}
        new["version"] = cur["version"] + 1
        new["pipeline_schema_version"] = PIPELINE_SCHEMA_VERSION
        # 검토 내용은 요청이 전부 정한다(이전 version의 expected 등을 물려받지 않는다). 멱등 비교가 요청 대 최신 version이 된다.
        for k in ("expected", "expected_kind", "reviewed_input"):
            new.pop(k, None)
        new["review_status"] = decision
        if failure_type:
            new["failure_type"] = failure_type
        if expected is not None:
            new["expected"] = expected
        if expected is not None or reviewed_input is not None:
            new["expected_kind"] = expected_kind
        if reviewed_input is not None:
            new["reviewed_input"] = reviewed_input     # 검토자가 직접 쓴 개인정보 없는 입력. 비식별 단계는 건드리지 않는다
        ext = dict(cur.get("ext") or {})                 # 다른 영역의 ext 보존
        mine = dict(ext.get("ops-feedback") or {})
        mine["review"] = {"reviewer_role": reviewer_role, **({"note": note} if note else {})}
        mine["reviewed_by"] = reviewed_by or reviewer_role      # 사람(X-Openit-User) 또는 토큰 역할
        ext["ops-feedback"] = mine
        new["ext"] = ext
        # 같은 검토(review_status·expected·expected_kind·reviewed_input이 최신 version과 모두 같다)를 다시 보내면
        # 새 version도, reviewed_by 변경도 없이 기존 레코드를 돌려준다. failure_type은 지정했을 때만 비교한다.
        if (cur["review_status"] == decision and cur.get("expected") == new.get("expected")
                and cur.get("expected_kind") == new.get("expected_kind")
                and cur.get("reviewed_input") == new.get("reviewed_input")
                and (failure_type is None or failure_type == cur["failure_type"])):
            return cur
        new["created_at"] = self._clock()
        return self._store.publish_case(new)

    def validate_expected(self, case: dict, expected, kind: Optional[str]) -> None:
        """expected가 expected_kind의 출력 스키마(LORA_A: ActionExtraction 객체, LORA_B: ResponseText 문자열)를 지키는지 확인한다."""
        if kind not in _OUTPUT_DEF:
            raise BoundaryError("INVALID_RECORD", f"expected를 적을 때는 expected_kind({_KINDS})도 필요하다")
        self._check(_OUTPUT_DEF[kind], expected, "expected")

    def validate_reviewed_input(self, case: dict, reviewed_input: dict, kind: str) -> None:
        """reviewed_input이 expected_kind의 입력 스키마(LORA_A: ActionExtractionInput, LORA_B: ResponseRequest)를 지키는지 확인한다."""
        self._check(_INPUT_DEF[kind], reviewed_input, "reviewed_input")

    @staticmethod
    def _check(name: str, value, what: str) -> None:
        try:
            validate_record(name, value)
        except BoundaryError as e:
            raise BoundaryError("INVALID_RECORD", f"{what}가 경계 계약 {name}을 위반한다", e.details) from None

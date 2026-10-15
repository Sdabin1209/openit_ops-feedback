"""사람 검토 큐. 검토 결과는 version을 올린 새 FailureCase로 게시한다(레코드는 불변, D25)."""
from __future__ import annotations

from typing import Optional

from boundary_contracts.errors import BoundaryError
from jsonschema import Draft202012Validator

from boundary_contracts import PIPELINE_SCHEMA_VERSION

from .clock import Clock, utc_now
from .failurestore import FailureStoreImpl
from .schemas import trace_schema


_INPUT_OF = {"PARSER": ("parser_input", "ParserOutput"), "GENERATOR": ("generator_input", "GeneratorOutput")}


class ReviewQueue:
    def __init__(self, store: FailureStoreImpl, *, clock: Clock = utc_now) -> None:
        self._store = store
        self._clock = clock

    def pending(self, *, failure_type: Optional[str] = None, source_release_id: Optional[str] = None,
                cursor: Optional[str] = None, limit: int = 50):
        return self._store.list_cases(review_status="PENDING", failure_type=failure_type,
                                      source_release_id=source_release_id, cursor=cursor, limit=limit)

    def review(self, case_id: str, decision: str, reviewer_role: str, *, base_version: Optional[int] = None,
               failure_type: Optional[str] = None, expected: Optional[dict] = None, expected_kind: Optional[str] = None, note: Optional[str] = None) -> dict:
        if decision not in ("CONFIRMED", "DISMISSED", "PENDING"):
            raise BoundaryError("INVALID_RECORD", "decision은 CONFIRMED, DISMISSED, PENDING 중 하나")
        if decision != "CONFIRMED" and expected is not None:
            raise BoundaryError("INVALID_RECORD", f"{decision}에는 expected를 둘 수 없다(CONFIRMED에만)")
        if expected is None and expected_kind is not None:
            raise BoundaryError("INVALID_RECORD", "expected_kind는 expected와 함께만 쓴다")
        cur = self._store.get_case(case_id)
        if base_version is not None and base_version != cur["version"]:
            raise BoundaryError("CONFLICT", f"최신 version은 {cur['version']}")
        if expected is not None:
            self.validate_expected(cur, expected, expected_kind)
        new = {k: v for k, v in cur.items() if k != "ext"}
        new["version"] = cur["version"] + 1
        new["pipeline_schema_version"] = PIPELINE_SCHEMA_VERSION
        new["review_status"] = decision
        if failure_type:
            new["failure_type"] = failure_type
        if expected is not None:
            new["expected"] = expected
            new["expected_kind"] = expected_kind
        ext = dict(cur.get("ext") or {})                 # 다른 영역의 ext 보존
        mine = dict(ext.get("ops-feedback") or {})
        mine["review"] = {"reviewer_role": reviewer_role, **({"note": note} if note else {})}
        ext["ops-feedback"] = mine
        new["ext"] = ext
        # 같은 검토를 다시 보내면(재시도) 새 version을 만들지 않는다.
        if (cur["review_status"] == decision and cur.get("failure_type") == new["failure_type"]
                and cur.get("expected") == new.get("expected") and cur.get("expected_kind") == new.get("expected_kind") and cur.get("ext") == ext):
            return cur
        new["created_at"] = self._clock()
        return self._store.publish_case(new)

    def validate_expected(self, case: dict, expected: dict, kind: Optional[str]) -> None:
        """expected가 비식별 trace의 해당 입력(parser_input|generator_input)에 대한 문진 스키마 출력인지 확인한다."""
        if kind not in _INPUT_OF:
            raise BoundaryError("INVALID_RECORD", "expected를 적을 때는 expected_kind(PARSER|GENERATOR)도 필요하다")
        field, out_def = _INPUT_OF[kind]
        trace = self._store.get_trace(case["deidentified_trace_ref"])
        if trace.get(field) is None:
            raise BoundaryError("INVALID_RECORD", f"이 사례의 trace에는 {field}가 없어 expected_kind={kind}를 적을 수 없다")
        schema = trace_schema(case["runtime_contract_version"])
        v = Draft202012Validator({"$ref": f"#/$defs/{out_def}", "$defs": schema["$defs"]})
        errs = sorted(v.iter_errors(expected), key=lambda e: list(e.absolute_path))
        if errs:
            raise BoundaryError("INVALID_RECORD", f"expected가 문진 스키마 {out_def}를 위반한다",
                                {"errors": [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.validator}" for e in errs[:5]]})

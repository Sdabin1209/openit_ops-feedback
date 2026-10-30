"""수집 업무: 받은 TraceEnvelope 하나를 원본 기록(RawTraceRecord)으로 만든다. 입력 TraceEnvelope+받은 시각+지원 버전, 출력 RawTraceRecord.

envelope가 계약을 지키는지는 스키마(TraceIntakeRequest)가 정한다. 스키마로 표현하지 못하는 것(schemas/README.md 표): 문진 스키마 버전이 데이터가 정하는 동적 참조라는 점,
envelope 안팎의 값이 서로 같아야 한다는 교차 필드 검사.
"""
from __future__ import annotations

from functools import lru_cache

import survey_schema
from boundary_contracts.canonical import sha256_hash
from boundary_contracts.errors import BoundaryError
from jsonschema import Draft202012Validator

from ..types.RawTraceRecord import RawTraceRecord
from ..types.TraceIntakeRequest import TraceIntakeRequest
from ..wiring.validate import brief, check


@lru_cache(maxsize=None)
def _trace_validator(version: int) -> Draft202012Validator:
    # 왜 설치된 문진 스키마 패키지에서 읽나: 사본을 두면 문진 스키마가 바뀔 때 어긋난다(D110). 버전은 envelope가 정한다.
    try:
        return Draft202012Validator(survey_schema.load_schema("TurnTrace", version))
    except KeyError:
        raise BoundaryError("UNSUPPORTED_VERSION", f"지원하지 않는 문진 스키마 버전: {version}") from None


def accept(request: TraceIntakeRequest) -> RawTraceRecord:
    check("TraceIntakeRequest", request)
    envelope = request["envelope"]
    ver = envelope["runtime_contract_version"]
    if ver not in request["supported_versions"]:
        raise BoundaryError("UNSUPPORTED_VERSION", f"지원하지 않는 문진 스키마 버전: {ver}", {"supported": request["supported_versions"]})
    trace = envelope["trace"]
    errs = list(_trace_validator(ver).iter_errors(trace))
    if errs:
        raise BoundaryError("INVALID_RECORD", f"TurnTrace(v{ver}) 스키마 위반", {"errors": [brief(e) for e in errs[:5]]})
    sid, tid = envelope["session_id"], envelope["turn_id"]
    if trace.get("session_id") != sid or trace.get("turn_id") != tid:
        raise BoundaryError("INVALID_RECORD", "envelope와 trace의 session_id/turn_id가 다르다")
    if trace.get("contract_version") != ver:
        raise BoundaryError("INVALID_RECORD", "trace.contract_version이 envelope.runtime_contract_version과 다르다")
    out: RawTraceRecord = {"session_id": sid, "turn_id": tid, "release_id": envelope["release_id"], "received_at": request["received_at"],
                           "envelope": envelope, "envelope_hash": sha256_hash(envelope)}
    return out

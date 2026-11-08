"""수집 업무: 받은 TraceEnvelope 하나를 원본 기록(RawTraceRecord)으로 만든다. 입력 TraceEnvelope+받은 시각+지원 버전, 출력 RawTraceRecord.

envelope가 계약을 지키는지는 스키마(TraceIntakeRequest)가 정한다. 스키마로 표현하지 못하는 것(schemas/README.md 표): 문진 스키마 버전이 데이터가 정하는 동적 참조라는 점,
envelope 안팎의 값이 서로 같아야 한다는 교차 필드 검사.
"""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache

import survey_schema
from boundary_contracts import load_schema
from boundary_contracts.canonical import sha256_hash
from boundary_contracts.errors import BoundaryError
from jsonschema import Draft202012Validator

from ..types.RawTraceRecord import RawTraceRecord
from ..types.TraceIntakeRequest import TraceIntakeRequest
from ..wiring.validate import brief, check
from boundary_contracts import format_checker as _bc_format_checker  # 형식(format) 검증기가 없으면 조용히 넘기지 않고 실패한다(계약 3.5.0)


@lru_cache(maxsize=None)
def _trace_validator(version: int) -> Draft202012Validator:
    # 왜 설치된 문진 스키마 패키지에서 읽나: 사본을 두면 문진 스키마가 바뀔 때 어긋난다(D110). 버전은 envelope가 정한다.
    try:
        return Draft202012Validator(survey_schema.load_schema("TurnTrace", version), format_checker=_bc_format_checker(survey_schema.load_schema("TurnTrace", version)))
    except KeyError:
        raise BoundaryError("UNSUPPORTED_VERSION", f"지원하지 않는 문진 스키마 버전: {version}") from None


@lru_cache(maxsize=1)
def _id_rule() -> tuple:
    # 왜 계약에서 읽나: Id 형식을 코드에 복사하면 계약이 바뀔 때 어긋난다(구조 규칙: 계약 사본 금지).
    d = load_schema("_common")["$defs"]["Id"]
    return re.compile(d["pattern"]), d["maxLength"]


def envelope_id(original: str) -> str:
    """봉투에 실리는 외부 식별자 값. 계약 boundary-contracts/CONVENTIONS.md 10절 "외부 식별자를 봉투에 넣을 때"(116행): Id 형식(128자 이하)에 맞으면 그대로,
    맞지 않으면 `x-` + 원본 UTF-8 SHA-256 앞 32자(소문자 16진). 원본은 trace 본문에 남는다. 계약 패키지에 이 함수가 생기면(계약 5.0.0 예정) 그쪽으로 옮긴다."""
    pattern, max_len = _id_rule()
    if len(original) <= max_len and pattern.match(original):
        return original
    return "x-" + hashlib.sha256(original.encode("utf-8")).hexdigest()[:32]


def accept(request: TraceIntakeRequest) -> RawTraceRecord:
    check("TraceIntakeRequest", request)
    envelope = request["envelope"]
    ver = envelope["runtime_contract_version"]
    if ver not in request["supported_versions"]:
        raise BoundaryError("UNSUPPORTED_VERSION", f"지원하지 않는 문진 스키마 버전: {ver}", {"supported": request["supported_versions"]})
    trace = envelope["trace"]
    errs = list(_trace_validator(ver).iter_errors(trace))
    if errs:
        briefs = [brief(e) for e in errs[:5]]      # 경로와 규칙 종류뿐(값 없음). 메시지에도 실어 호출자와 서버 로그가 어긋난 칸을 안다(대장 #20)
        raise BoundaryError("INVALID_RECORD", f"TurnTrace(v{ver}) 스키마 위반: " + "; ".join(briefs[:3]), {"errors": briefs})
    sid, tid = envelope["session_id"], envelope["turn_id"]
    # 왜 같음이 아니라 변환 규칙인가: 바깥이 정한 ID 가 Id 형식에 안 맞으면 봉투에는 x- 변환값이 실리고 원본은 trace 에 있다(CONVENTIONS 10절). 같음 비교는 그 세션을 전부 거부했다.
    # trace 에 ID 가 없으면 비교할 원본이 없어 거부한다(None 은 규칙에 넣지 않는다).
    t_sid, t_tid = trace.get("session_id"), trace.get("turn_id")
    if not isinstance(t_sid, str) or not isinstance(t_tid, str) or envelope_id(t_sid) != sid or envelope_id(t_tid) != tid:
        raise BoundaryError("INVALID_RECORD", "envelope의 session_id/turn_id가 trace 원본 값에 계약 규칙(CONVENTIONS 10절)을 적용한 값과 다르다")
    # 왜 trace.contract_version == envelope.runtime_contract_version 비교가 없나(D117: 코드 안 거부 조건은 계약에 있는 것만): 계약에 그 같음을 요구하는 문장이 없다.
    # TraceEnvelope 는 "trace 는 runtime_contract_version 에 해당하는 문진 스키마의 TurnTrace 로 별도 검증한다"까지만 정하고(위 _trace_validator 가 그것을 한다),
    # TurnTrace.contract_version 은 {integer, minimum 1} 일 뿐 const 가 아니다. 같음 보장은 계약이 주지 않는다: 필요하면 헤드가 계약에 넣는다(변경 요청).
    out: RawTraceRecord = {"session_id": sid, "turn_id": tid, "release_id": envelope["release_id"], "received_at": request["received_at"],
                           "envelope": envelope, "envelope_hash": sha256_hash(envelope)}
    return out

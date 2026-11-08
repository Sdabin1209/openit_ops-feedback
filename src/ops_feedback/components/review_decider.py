"""사람 검토 결정 적용. 입력 ReviewRequest(최신 version의 사례+검토 결정), 출력 ReviewOutcome(새 version 레코드 또는 변경 없음).
검토 결과는 version을 올린 새 FailureCase로 게시한다(레코드는 불변, D25). 게시는 wiring이 저장소에 한다.
결정의 상태·정답·종류 조건과 정답·검토 입력의 형식(종류별 계약 타입)은 스키마(ReviewDecision)가 정한다.
"""
from __future__ import annotations

import re
from typing import Optional

from boundary_contracts import PIPELINE_SCHEMA_VERSION
from boundary_contracts.errors import BoundaryError
from boundary_contracts.review_input import review_value_problems

from ..types.ReviewOutcome import ReviewOutcome
from ..types.ReviewRequest import ReviewRequest
from ..wiring.validate import check

# 왜 개인정보 패턴·문자열 길이·깊이를 여기서 하지 않나(D117): 검토 값의 이 거부 기준은 계약이 정한다(INTERFACES I7 '검토 게시 값의 판정 기준' 1항, boundary_contracts.review_input.review_value_problems,
# 규칙 데이터 data/review_input_rules.json). 4.x 까지 이 파일에 있던 사본(전화·주민번호 패턴, 이메일 선형 탐지, 20000자, 깊이 64)을 지웠다.
# 아래 원문 포함 거부(2항)만 이 영역이 한다: 환자 발화 원문은 이 영역의 검토 사본에만 있어 계약이 함수로 줄 수 없고, 판정 기준만 계약에 있다.
_WS = re.compile(r"\s+")
MIN_ORIGINAL_CHARS = 10      # 부분 포함으로 거부하는 원문의 최소 길이(공백 제외). 계약 I7 가 ops-feedback 의 설계 값으로 선언한 값, 측정 안 함


def _strings(v):
    """값 안의 모든 문자열(객체 키 포함). 깊이는 앞서 계약 검사(review_value_problems)가 64 로 막았으므로 재귀해도 안전하다."""
    if isinstance(v, str):
        yield v
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x)
    elif isinstance(v, dict):
        for k, x in v.items():
            yield str(k)
            yield from _strings(x)


class ReviewDecider:
    def _reject_value_problems(self, value, what: str) -> None:
        """계약 검사(JSON 공통 규칙·문자열 길이·개인정보 패턴). 오류에는 위반 이름만 싣고 값은 싣지 않는다(개인정보일 수 있다, I7)."""
        try:
            problems = review_value_problems(value)
        except BoundaryError as e:
            raise BoundaryError("INVALID_RECORD", f"{what}가 계약 JSON 규칙을 어긴다: {e.code}") from None
        if problems:
            raise BoundaryError("INVALID_RECORD", f"{what}가 검토 값 규칙을 어긴다({', '.join(problems)}). 개인정보 없이 다시 쓴다")

    def _reject_preserved(self, value, originals: list) -> None:
        """reviewed_input은 읽기 역할 전체가 읽는 FailureCase에 실린다. 검토 사본에만 두기로 한 환자 발화 원문이 그대로 들어 있으면 reviewer 전용 경계가 무너지므로 거부한다.
        한계(측정하지 않은 보수 값): 10자 미만 발화는 다른 문장의 일부와 구별되지 않아 완전히 같은 문자열일 때만 막고, 바꿔 쓴 문장은 막지 못한다(개인정보 없는 문장을 쓰는 것은 검토자 책임)."""
        squashed = [c for c in (_WS.sub("", o) for o in originals) if c]      # 계약 I7 2항: 공백을 뺀 원문으로 비교한다
        for s_ in _strings(value):
            t = _WS.sub("", s_)
            for o in squashed:
                if t == o or (len(o) >= MIN_ORIGINAL_CHARS and o in t):
                    raise BoundaryError("INVALID_RECORD", "reviewed_input에 환자 발화 원문이 그대로 들어 있다. 개인정보 없이 다시 쓴다")

    def decide(self, request: ReviewRequest) -> ReviewOutcome:
        check("ReviewRequest", request)
        cur, d = request["current"], request["decision"]
        for what in ("expected", "reviewed_input", "note"):      # 계약 검사가 먼저다: 깊이 64 를 넘는 값을 아래 순회가 보지 않게 한다
            if d.get(what):
                self._reject_value_problems(d[what], what)
        if d.get("reviewed_input"):
            self._reject_preserved(d["reviewed_input"], request.get("preserved_utterances") or [])
        base = d.get("base_version")
        if base is not None and base != cur["version"]:
            raise BoundaryError("CONFLICT", f"최신 version은 {cur['version']}")
        new = {k: v for k, v in cur.items() if k != "ext"}
        new["version"] = cur["version"] + 1
        new["pipeline_schema_version"] = PIPELINE_SCHEMA_VERSION
        # 검토 내용은 요청이 전부 정한다(이전 version의 expected 등을 물려받지 않는다). 멱등 비교가 요청 대 최신 version이 된다.
        for k in ("expected", "expected_kind", "reviewed_input"):
            new.pop(k, None)
        new["review_status"] = d["review_status"]
        if d.get("failure_type"):
            new["failure_type"] = d["failure_type"]
        if "expected" in d:
            new["expected"] = d["expected"]
        if "expected_kind" in d:
            new["expected_kind"] = d["expected_kind"]
        if "reviewed_input" in d:
            new["reviewed_input"] = d["reviewed_input"]     # 검토자가 직접 쓴 개인정보 없는 입력. 비식별 단계는 건드리지 않는다
        ext = dict(cur.get("ext") or {})                 # 다른 영역의 ext 보존
        mine = dict(ext.get("ops-feedback") or {})
        mine["review"] = {"reviewer_role": d["reviewer_role"], **({"note": d["note"]} if d.get("note") else {})}
        mine["reviewed_by"] = d.get("reviewed_by") or d["reviewer_role"]      # 사람(X-Openit-User) 또는 토큰 역할
        ext["ops-feedback"] = mine
        new["ext"] = ext
        try:
            check("OpsFeedbackFailureCaseExt", mine, contract=True)      # 생산자 검증(R7): 쓰는 ext가 계약을 지키는지 게시 전에 본다
        except BoundaryError:
            raise BoundaryError("UNAVAILABLE", "사례 ext가 OpsFeedbackFailureCaseExt를 어긴다(서버 결함)") from None
        # 같은 검토(review_status·expected·expected_kind·reviewed_input이 최신 version과 모두 같다)를 다시 보내면
        # 새 version도, reviewed_by 변경도 없이 기존 레코드를 돌려준다. failure_type은 지정했을 때만 비교한다.
        if (cur["review_status"] == d["review_status"] and cur.get("expected") == new.get("expected")
                and cur.get("expected_kind") == new.get("expected_kind")
                and cur.get("reviewed_input") == new.get("reviewed_input")
                and (d.get("failure_type") is None or d["failure_type"] == cur["failure_type"])):
            return {"changed": False, "case": cur}
        new["created_at"] = request["now"]
        out: ReviewOutcome = {"changed": True, "case": new}
        check("ReviewOutcome", out)
        return out

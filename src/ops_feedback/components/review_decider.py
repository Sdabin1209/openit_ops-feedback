"""사람 검토 결정 적용. 입력 ReviewRequest(최신 version의 사례+검토 결정), 출력 ReviewOutcome(새 version 레코드 또는 변경 없음).
검토 결과는 version을 올린 새 FailureCase로 게시한다(레코드는 불변, D25). 게시는 wiring이 저장소에 한다.
결정의 상태·정답·종류 조건과 정답·검토 입력의 형식(종류별 계약 타입)은 스키마(ReviewDecision)가 정한다.
"""
from __future__ import annotations

import re
from typing import Optional

from boundary_contracts import PIPELINE_SCHEMA_VERSION
from boundary_contracts.errors import BoundaryError

from ..types.ReviewOutcome import ReviewOutcome
from ..types.ReviewRequest import ReviewRequest
from ..wiring.validate import check

# 검토자가 직접 쓰는 expected·reviewed_input은 비식별 처리기를 거치지 않으므로, 개인정보 패턴(이메일·전화·주민번호)이 든 값은 받지 않는다.
# 이름·주소 같은 것은 패턴으로 알 수 없다: 검토 화면 안내와 사람 책임이다(docs/boundary.md). 값은 오류에 싣지 않는다.
# 시간: 전화·주민번호 패턴은 한도가 정해진 반복만 있어 입력 길이에 선형이다. 이메일은 '@'가 없으면 정규식 자체를 돌리지 않고, '@' 하나마다
# 앞뒤 고정 길이 창만 본다(점검 round2 M2: 앞쪽 무제한 문자 클래스 정규식은 '@' 없는 긴 문자열에서 제곱 시간이었다).
# 문자열 하나의 길이는 스키마 ReviewDecision.Bounded의 maxLength 20,000자로 입구에서 막는다(검토자가 쓰는 문장·JSON 값으로 충분한 임의 상한, 측정 안 함, docs/adr/0008).
_EMAIL_LOCAL = re.compile(r"[A-Za-z0-9._%+-]")
_EMAIL_TAIL = re.compile(r"[A-Za-z0-9.-]{0,254}")
DEFAULT_REVIEW_PATTERNS = {
    "phone_kr": r"0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}",
    "rrn_kr": r"\d{6}[- ]?[1-8]\d{6}",
}


def _has_email(s: str) -> bool:
    """local@domain.tld 모양이 있는가. '@' 위치마다 앞 문자 하나와 뒤 최대 254자만 본다(선형)."""
    i = s.find("@")
    while i != -1:
        if i > 0 and _EMAIL_LOCAL.match(s[i - 1]):
            dom = _EMAIL_TAIL.match(s, i + 1).group(0)
            k = dom.rfind(".")
            # 마지막 '.' 뒤가 영문 2자 이상이면 정규식 `\.[A-Za-z]{2,}`와 같은 뜻(그 앞 도메인 부분은 길이 제한 없는 `[A-Za-z0-9.-]*`)
            while k != -1:
                if k >= 1 and dom[k + 1:k + 3].isalpha() and dom[k + 1:k + 3].isascii() and len(dom[k + 1:k + 3]) == 2:
                    return True
                k = dom.rfind(".", 0, k)
        i = s.find("@", i + 1)
    return False


def _strings(v, depth=0):
    if depth > 64:
        raise BoundaryError("INVALID_RECORD", "검토 입력의 중첩이 너무 깊다")
    if isinstance(v, str):
        yield v
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x, depth + 1)
    elif isinstance(v, dict):
        for k, x in v.items():
            yield str(k)
            yield from _strings(x, depth + 1)


class ReviewDecider:
    def __init__(self, *, residual_patterns: Optional[dict] = None) -> None:
        # 호출자가 준 패턴은 `email`이라는 이름이면 위의 선형 탐지로 대신하고, 나머지는 정규식 그대로 쓴다(길이는 스키마의 20000자 상한으로 이미 묶였다).
        self._pii = {n: re.compile(p) for n, p in (residual_patterns if residual_patterns is not None else DEFAULT_REVIEW_PATTERNS).items() if n != "email"}

    def _reject_pii(self, value, what: str) -> None:
        for s_ in _strings(value):
            if _has_email(s_):
                raise BoundaryError("INVALID_RECORD", f"{what}에 개인정보로 보이는 패턴(email)이 있다. 개인정보 없이 다시 쓴다")
            for name, rx in self._pii.items():
                if rx.search(s_):
                    raise BoundaryError("INVALID_RECORD", f"{what}에 개인정보로 보이는 패턴({name})이 있다. 개인정보 없이 다시 쓴다")

    def decide(self, request: ReviewRequest) -> ReviewOutcome:
        check("ReviewRequest", request)
        cur, d = request["current"], request["decision"]
        for what in ("expected", "reviewed_input", "note"):
            if d.get(what):
                self._reject_pii(d[what], what)
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

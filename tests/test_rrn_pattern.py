"""B7(표준성 점검 2026-10-29): 주민등록번호 모양 패턴은 외국인등록번호(뒷자리 첫째 5~8)도 잡는다.
체계(뒷자리 첫째 1·2 = 1900년대 내국인, 3·4 = 2000년대 내국인, 5·6 / 7·8 = 외국인 1900년대 / 2000년대)는 뉴스 기사와 개인 글로만 확인했고 법령 원문은 확인하지 못했다."""
import re

import pytest
import yaml

from ops_feedback.review import DEFAULT_REVIEW_PATTERNS

EXAMPLE = yaml.safe_load(open("config/job.example.yaml", encoding="utf-8"))["deid"]["residual_patterns"]["rrn_kr"]


@pytest.mark.parametrize("pat", [EXAMPLE, DEFAULT_REVIEW_PATTERNS["rrn_kr"]], ids=["job.example", "review.default"])
@pytest.mark.parametrize("first", "12345678")
@pytest.mark.parametrize("sep", ["", "-", " "])
def test_matches_first_digit_1_to_8(pat, first, sep):
    assert re.search(pat, f"900101{sep}{first}123456")


@pytest.mark.parametrize("pat", [EXAMPLE, DEFAULT_REVIEW_PATTERNS["rrn_kr"]], ids=["job.example", "review.default"])
def test_does_not_match_unrelated_digits(pat):
    assert not re.search(pat, "123")

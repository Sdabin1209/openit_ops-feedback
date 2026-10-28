"""B4(표준성 점검 2026-10-29): 의사 식별자는 HMAC-SHA256(키=salt)이다(RFC 2104, FIPS 198-1).
기대값은 표준 라이브러리 `hmac`·`hashlib`·`json`으로 이 시험 안에서 따로 계산한다(구현의 함수를 호출해 만든 값이 아니다)."""
import hashlib
import hmac
import json

from ops_feedback import admin
from ops_feedback.deid import pseudonym_of

SALT = "unit-test-salt-0123456789abcdef0123456789abcdef"


def independent(salt: str, key: str, value: str) -> str:
    # 메시지 = {"key":..., "value":...}를 키 정렬·공백 없음·비ASCII 그대로(RFC 8785와 같은 문자열 직렬화) 직렬화한 UTF-8.
    msg = json.dumps({"key": key, "value": value}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "p-" + hmac.new(salt.encode("utf-8"), msg, hashlib.sha256).hexdigest()[:32]


def test_pseudonym_equals_independent_hmac_sha256():
    for key, value in [("session_id", "s-1"), ("turn_id", "t-9"), ("session_id", "세션-한글"), ("case", "x" * 500)]:
        assert pseudonym_of(SALT, key, value) == independent(SALT, key, value)


def test_pseudonym_depends_on_key_and_salt_and_value():
    base = pseudonym_of(SALT, "session_id", "s-1")
    assert base != pseudonym_of(SALT, "turn_id", "s-1")
    assert base != pseudonym_of(SALT + "x", "session_id", "s-1")
    assert base != pseudonym_of(SALT, "session_id", "s-2")


def test_admin_delete_path_uses_same_value():
    assert admin.session_pseudonym(SALT, "s-1") == independent(SALT, "session_id", "s-1")

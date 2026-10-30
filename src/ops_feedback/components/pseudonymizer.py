"""의사 식별자 계산의 유일한 정의. 입력 PseudonymRequest{salt,key,value}, 출력 Pseudonym.

비식별 처리기(wiring이 이 함수를 주입), 사례 ID 만들기, 관리 도구의 세션 삭제가 모두 이 정의를 쓴다.
왜 한 곳: 삭제가 식을 따로 구현하면 한쪽만 바뀔 때 개인정보 삭제가 조용히 0건이 된다(점검 OF-03).
식: HMAC-SHA256(키=salt, 메시지=JCS({key, value}))의 앞 128비트 16진 32자. 키 있는 해시의 표준은 HMAC이다(RFC 2104, FIPS 198-1)이고
salt를 메시지 앞에 붙인 SHA-256 직접 구성은 표준이 아니라 바꿨다(표준성 점검 B4, ADR 0016). 128비트 절단은 HMAC 출력 절단 허용(RFC 2104 5절)을
따른 것이며 길이 자체는 기존 형식(`p-`+32자)을 지키려는 선택이다.
"""
from __future__ import annotations

import hashlib
import hmac

from boundary_contracts.canonical import canonicalize

from ..types.PseudonymRequest import PseudonymRequest
from ..wiring.validate import check

# 출처: 헤드 결정(표준성 점검 2026-10-29 B6), ADR 0017. 키 길이 하한 32바이트(256비트)는 HMAC-SHA256의 해시 출력 길이와 같게 잡은 값이다(RFC 2104는 키를
# 해시 출력 길이 이상으로 권한다고 알고 있으나 원문 대조 전). 측정하지 않았다. CSPRNG로 만들었는지는 코드가 알 수 없어 길이만 검사한다.
MIN_SALT_BYTES = 32


def pseudonymize(request: PseudonymRequest) -> str:
    check("PseudonymRequest", request)
    msg = canonicalize({"key": request["key"], "value": request["value"]})
    out = "p-" + hmac.new(request["salt"].encode("utf-8"), msg, hashlib.sha256).hexdigest()[:32]
    check("Pseudonym", out)
    return out


def check_salt(salt: str) -> None:
    """기동 때 salt 길이를 검사한다(UTF-8 바이트). 바이트 수는 스키마의 minLength(문자 수)로 표현하지 못한다. 오류에는 길이 요구만 싣고 값은 싣지 않는다."""
    if len(salt.encode("utf-8")) < MIN_SALT_BYTES:
        raise ValueError(f"의사 식별자 salt가 너무 짧다: {MIN_SALT_BYTES}바이트 이상이어야 한다(CSPRNG로 만든 무작위 값, ADR 0017)")

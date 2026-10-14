"""RFC 8785(JCS) 정규화와 SHA-256 해시.

모든 SDK와 서비스가 같은 해시를 내려면 구현이 하나여야 한다. 이 파일이 그 기준이다.
JSON 값은 dict, list, str, bool, None, int, float만 허용한다. 정수는 IEEE 754 배정밀도로
정확히 표현되는 범위(절댓값 2**53 이하)만 허용하고, NaN과 무한대는 거부한다.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

_MAX_SAFE_INT = 2**53


def _number(x: int | float) -> str:
    if isinstance(x, int):
        if abs(x) > _MAX_SAFE_INT:
            raise ValueError(f"정수가 IEEE 754로 정확히 표현되는 범위를 벗어난다: {x}")
        return str(x)
    if not math.isfinite(x):
        raise ValueError("NaN과 무한대는 JSON에 없다")
    if x == 0:
        return "0"  # -0도 0
    sign = "-" if x < 0 else ""
    # 최단 왕복 표현의 자릿수와 지수를 얻은 뒤 ES6 Number::toString 규칙으로 쓴다.
    t = Decimal(repr(abs(x))).as_tuple()
    digits = "".join(map(str, t.digits)).rstrip("0") or "0"
    n = len(t.digits) + t.exponent  # 값 = 0.digits * 10**n
    k = len(digits)
    if k <= n <= 21:
        body = digits + "0" * (n - k)
    elif 0 < n <= 21:
        body = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        exp = ("+" if e >= 0 else "-") + str(abs(e))
        body = digits + "e" + exp if k == 1 else digits[0] + "." + digits[1:] + "e" + exp
    return sign + body


def _string(s: str) -> str:
    try:
        s.encode("utf-8")
    except UnicodeEncodeError as e:  # 짝 없는 서로게이트
        raise ValueError("유효하지 않은 유니코드 문자열") from e
    return json.dumps(s, ensure_ascii=False)


def _write(v: Any, out: list[str]) -> None:
    if v is None:
        out.append("null")
    elif v is True:
        out.append("true")
    elif v is False:
        out.append("false")
    elif isinstance(v, (int, float)):
        out.append(_number(v))
    elif isinstance(v, str):
        out.append(_string(v))
    elif isinstance(v, (list, tuple)):
        out.append("[")
        for i, item in enumerate(v):
            if i:
                out.append(",")
            _write(item, out)
        out.append("]")
    elif isinstance(v, dict):
        if not all(isinstance(k, str) for k in v):
            raise ValueError("객체의 키는 문자열이어야 한다")
        out.append("{")
        # 키는 UTF-16 코드 단위 순서로 정렬한다(big-endian 바이트 비교와 같다).
        for i, key in enumerate(sorted(v, key=lambda k: k.encode("utf-16-be"))):
            if i:
                out.append(",")
            out.append(_string(key))
            out.append(":")
            _write(v[key], out)
        out.append("}")
    else:
        raise ValueError(f"JSON으로 표현할 수 없는 타입: {type(v).__name__}")


def canonicalize(value: Any) -> bytes:
    """JCS 정규화한 UTF-8 바이트."""
    out: list[str] = []
    _write(value, out)
    return "".join(out).encode("utf-8")


def sha256_hash(value: Any) -> str:
    """JSON 값의 해시: 'sha256:' + 소문자 16진 64자."""
    return "sha256:" + hashlib.sha256(canonicalize(value)).hexdigest()


def content_hash(record: dict, field: str = "content_hash") -> str:
    """자기 자신의 해시 필드를 뺀 객체의 해시. 레코드의 content_hash를 계산할 때 쓴다."""
    return sha256_hash({k: v for k, v in record.items() if k != field})


def file_hash(path: str | os.PathLike) -> str:
    """파일 바이트의 해시."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def directory_hash(path: str | os.PathLike) -> str:
    """디렉터리의 해시. 파일마다 {path(상대, '/' 구분), sha256}를 모아 경로 순으로 정렬한 목록의 JCS 해시.
    어댑터의 adapter_hash가 이 값이다. 빈 디렉터리와 숨김 파일 규칙은 따로 두지 않고 모든 일반 파일을 포함한다."""
    root = Path(path)
    entries = [
        {"path": p.relative_to(root).as_posix(), "sha256": file_hash(p)}
        for p in sorted(root.rglob("*"), key=lambda q: q.relative_to(root).as_posix())
        if p.is_file()
    ]
    return sha256_hash(entries)

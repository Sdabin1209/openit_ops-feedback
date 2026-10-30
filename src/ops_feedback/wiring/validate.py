"""스키마 검증 도구(업무 규칙 없음). 영역 스키마(`schemas/`, 리포 루트)와 계약 스키마(boundary_contracts 패키지)를 한 레지스트리에 올려 `$ref`가 풀리게 한다.

왜 한 곳: 컴포넌트의 입력 검사는 스키마 검증 하나여야 한다(DESIGN.md 5절). 검증 호출을 컴포넌트마다 다시 만들면 레지스트리와 오류 형식이 어긋난다.
영역 스키마는 `file:///ops/<이름>.schema.json`, 계약 스키마는 `file:///boundary/<이름>.schema.json`으로 올려 영역 스키마가 `../boundary/X.schema.json`으로 계약을 가리킨다.
스키마 위치: 소스 트리에서는 리포 루트 `schemas/`, 설치본에서는 `<prefix>/share/ops-feedback/schemas`(pyproject data-files, ADR 0024).
"""
from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from boundary_contracts import schema_dir as _contract_dir
from boundary_contracts.errors import BoundaryError
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

_OPS = "file:///ops/"
_BOUNDARY = "file:///boundary/"


def area_schema_dir() -> Path:
    here = Path(__file__).resolve()
    for c in (here.parents[3] / "schemas", Path(sys.prefix) / "share" / "ops-feedback" / "schemas"):
        if (c / "RawTraceRecord.schema.json").exists():
            return c
    raise FileNotFoundError("영역 스키마 폴더(schemas/)를 찾지 못했다")


@lru_cache(maxsize=1)
def _registry() -> Registry:
    reg = Registry()
    for base, d in ((_BOUNDARY, _contract_dir()), (_OPS, area_schema_dir())):
        for p in sorted(d.glob("*.schema.json")):
            reg = reg.with_resource(base + p.name, Resource.from_contents(json.loads(p.read_text("utf-8")), DRAFT202012))
    return reg


@lru_cache(maxsize=None)
def validator(name: str, *, contract: bool = False) -> Draft202012Validator:
    """영역 스키마(기본) 또는 계약 스키마(contract=True)의 검증기."""
    base, d = (_BOUNDARY, _contract_dir()) if contract else (_OPS, area_schema_dir())
    if not (d / f"{name}.schema.json").exists():
        raise ValueError(f"알 수 없는 스키마: {name}")
    return Draft202012Validator({"$ref": base + f"{name}.schema.json"}, registry=_registry(), format_checker=Draft202012Validator.FORMAT_CHECKER)


def brief(e) -> str:
    # 왜 값을 싣지 않나: 위반 값에 원본 조각이나 비밀이 있을 수 있어 경로와 규칙 종류만 낸다.
    return f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.validator}"


def check(name: str, value: Any, *, contract: bool = False, code: str = "INVALID_RECORD") -> None:
    """위반이면 BoundaryError(INVALID_RECORD). 영역 스키마가 기본, 계약 스키마는 contract=True."""
    errs = list(validator(name, contract=contract).iter_errors(value))
    if errs:
        errors = [brief(e) for e in sorted(errs, key=lambda e: list(map(str, e.absolute_path)))[:5]]
        raise BoundaryError(code, f"{name} 스키마 위반: " + "; ".join(errors[:3]), {"errors": errors})


@lru_cache(maxsize=None)
def contract_part(contract: str, pointer: str) -> Draft202012Validator:
    """계약 스키마의 한 부분(JSON Pointer, 예: `/properties/rollback`)을 검증하는 검증기. 영역이 같은 조건을 복사하지 않고 계약을 가리키게 한다."""
    return Draft202012Validator({"$ref": f"{_BOUNDARY}{contract}.schema.json#{pointer}"}, registry=_registry(), format_checker=Draft202012Validator.FORMAT_CHECKER)


def check_part(contract: str, pointer: str, value: Any, what: str, *, exc=ValueError) -> None:
    """계약 스키마의 한 부분으로 검사한다. 위반이면 `what`과 위반 경로·규칙 종류(값 없이)를 담은 exc."""
    errs = list(contract_part(contract, pointer).iter_errors(value))
    if errs:
        raise exc(f"{what}가 {contract}{pointer}를 어긴다: " + "; ".join(brief(e) for e in sorted(errs, key=lambda e: list(map(str, e.absolute_path)))[:5]))

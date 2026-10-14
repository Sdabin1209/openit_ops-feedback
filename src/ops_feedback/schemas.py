"""경계 계약 스키마와 문진 스키마(TurnTrace) 검증. 레코드는 쓰기 전에 여기서 검증한다."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from boundary_contracts import schema_dir
from boundary_contracts.errors import BoundaryError
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SCHEMA_ROOT = Path(__file__).parent / "_schemas"
_BASE = "file:///boundary/"


@lru_cache(maxsize=1)
def _registry() -> Registry:
    reg = Registry()
    for p in sorted(schema_dir().glob("*.schema.json")):
        reg = reg.with_resource(_BASE + p.name, Resource.from_contents(json.loads(p.read_text("utf-8")), DRAFT202012))
    return reg


@lru_cache(maxsize=None)
def _validator(name: str) -> Draft202012Validator:
    if not (schema_dir() / f"{name}.schema.json").exists():
        raise ValueError(f"알 수 없는 경계 스키마: {name}")
    return Draft202012Validator({"$ref": _BASE + f"{name}.schema.json"}, registry=_registry())


def _fmt(errors) -> str:
    errs = sorted(errors, key=lambda e: list(e.absolute_path))
    return "; ".join(f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message[:200]}" for e in errs[:5])


def validate_record(name: str, record: Any) -> None:
    """경계 계약 레코드를 검증한다. 위반이면 INVALID_RECORD."""
    errs = list(_validator(name).iter_errors(record))
    if errs:
        # 메시지에 값 조각이 섞일 수 있으므로 경로와 규칙 종류만 낸다.
        raise BoundaryError("INVALID_RECORD", f"{name} 스키마 위반", {"errors": [_brief(e) for e in errs[:5]]})


def _brief(e) -> str:
    return f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.validator}"


def supported_trace_versions() -> list[int]:
    return sorted(int(p.name[1:]) for p in (SCHEMA_ROOT / "survey").glob("v*") if p.is_dir())


@lru_cache(maxsize=None)
def trace_schema(version: int) -> dict:
    p = SCHEMA_ROOT / "survey" / f"v{version}" / "TurnTrace.json"
    if not p.exists():
        raise BoundaryError("UNSUPPORTED_VERSION", f"지원하지 않는 문진 스키마 버전: {version}")
    declared = int((p.parent / "CONTRACT_VERSION").read_text().strip())
    if declared != version:
        raise BoundaryError("UNSUPPORTED_VERSION", f"스키마 폴더 v{version}의 CONTRACT_VERSION이 {declared}")
    return json.loads(p.read_text("utf-8"))


@lru_cache(maxsize=None)
def _trace_validator(version: int) -> Draft202012Validator:
    return Draft202012Validator(trace_schema(version))


def validate_trace(version: int, trace: Any) -> None:
    """envelope.runtime_contract_version의 문진 스키마 TurnTrace로 검증한다."""
    v = _trace_validator(version)
    errs = list(v.iter_errors(trace))
    if errs:
        raise BoundaryError("INVALID_RECORD", f"TurnTrace(v{version}) 스키마 위반", {"errors": [_brief(e) for e in errs[:5]]})

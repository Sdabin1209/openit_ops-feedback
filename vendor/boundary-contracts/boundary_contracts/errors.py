"""경계 호출의 공통 오류(CONVENTIONS.md 3절). 모든 SDK와 서비스 클라이언트는 이것을 던진다."""
from __future__ import annotations

from typing import Any

CODES = {
    "NOT_FOUND": False,
    "INVALID_RECORD": False,
    "UNSUPPORTED_VERSION": False,
    "CONFLICT": False,
    "INTEGRITY_ERROR": False,
    "FAILED_PRECONDITION": False,
    "PERMISSION_DENIED": False,
    "UNAVAILABLE": True,
}


class BoundaryError(Exception):
    """code는 CODES의 키, retryable은 코드로 정해진다(UNAVAILABLE만 true)."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        if code not in CODES:
            raise ValueError(f"알 수 없는 오류 코드: {code}")
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details or {}

    @property
    def retryable(self) -> bool:
        return CODES[self.code]

    def to_record(self, pipeline_schema_version: str = "1.0") -> dict:
        """BoundaryError 스키마에 맞는 레코드."""
        rec = {"pipeline_schema_version": pipeline_schema_version, "code": self.code,
               "message": self.message, "retryable": self.retryable}
        if self.details:
            rec["details"] = self.details
        return rec

    @classmethod
    def from_record(cls, rec: dict) -> "BoundaryError":
        return cls(rec["code"], rec["message"], rec.get("details"))

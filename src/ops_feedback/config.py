"""설정 읽기(업무 규칙 없음). 비식별 기준, 보관 기간, 분류체계, 롤백 기준은 사용자가 정하므로 코드에 기본값이 없다.
받아들이는 조건은 모두 스키마가 정한다: 계약 OpsFeedbackJobConfig(키·형식·범위, 모르는 키 거부)와 영역 스키마 JobConfigChecks(비밀 직접 기입 금지, REASK_STREAK 조합)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml
from boundary_contracts import validator

from .wiring.validate import brief, validator as area_validator


def _path(e) -> str:
    out = ""
    for p in e.absolute_path:
        out += f"[{p}]" if isinstance(p, int) else (("." if out else "") + str(p))
    return out or "<root>"


def _check(v, cfg, what: str) -> None:
    # 왜 값을 싣지 않나: 위반 값이 비밀일 수 있어 경로와 규칙 종류만 낸다.
    errs = sorted(v.iter_errors(cfg), key=lambda e: list(map(str, e.absolute_path)))
    if errs:
        hint = " (비밀은 NAME_FILE로 주고 설정에는 {secret: NAME}만 쓴다)" if what == "JobConfigChecks" else ""
        raise ValueError(f"설정이 {what} 스키마를 어긴다: " + "; ".join(_path(e) + ": " + e.validator for e in errs[:5]) + hint)


def load_config(path: str | Path) -> dict:
    cfg = yaml.safe_load(Path(path).read_text("utf-8"))
    _check(area_validator("JobConfigChecks"), cfg, "JobConfigChecks")
    _check(validator("OpsFeedbackJobConfig"), cfg, "OpsFeedbackJobConfig")
    return cfg


def validate_server_env(environ) -> None:
    """서버 환경 변수 중 OPS_ 접두 변수를 OpsFeedbackServerEnv로 검증한다(모르는 이름은 거부: 오타로 보관 경고·검토자 신원 필수가 조용히 꺼지는 것을 막는다)."""
    env = {k: v for k, v in environ.items() if k.startswith("OPS_")}
    v = validator("OpsFeedbackServerEnv")
    if list(v.iter_errors({"env": env})):
        bad = sorted(k for k in env if not v.is_valid({"env": {k: env[k]}}))
        # 값은 비밀일 수 있어 변수 이름만 낸다
        raise ValueError("환경 변수가 OpsFeedbackServerEnv 스키마를 어긴다: " + ", ".join(bad or ["<root>"]))


def parse_read_tokens(spec: str) -> dict[str, str]:
    """'역할=토큰,역할=토큰' -> {토큰: 역할}"""
    out: dict[str, str] = {}
    for part in filter(None, (x.strip() for x in spec.split(","))):
        role, sep, tok = part.partition("=")
        if not sep or not role or not tok:
            raise ValueError("OPS_READ_TOKENS 형식은 역할=토큰,역할=토큰")
        out[tok] = role
    return out


class JsonFormatter(logging.Formatter):
    """한 줄 JSON. 메시지의 따옴표·개행을 이스케이프해 요청 경로로 로그 줄을 위조할 수 없다."""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({"ts": self.formatTime(record), "lvl": record.levelname, "msg": record.getMessage()}, ensure_ascii=False)

"""설정 파일 로더. 비식별 기준, 보관 기간, 분류체계, 롤백 기준은 사용자가 정하므로 코드에 기본값이 없다."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from boundary_contracts import validator

SECRET_KEY_PARTS = ("token", "secret", "password", "credential", "api_key", "authorization", "private_key")


def check_inline_secrets(value: Any, path: str = "config") -> None:
    """키 이름이 비밀처럼 보이는데 문자열 값이 직접 적혀 있으면 시작을 거부한다(D84).
    허용: {secret: NAME} / {env: NAME}(이름만), 이름만 받는 `*_env` 키. 오류에는 키 경로만 싣고 값은 싣지 않는다."""
    if isinstance(value, dict):
        if set(value) in ({"secret"}, {"env"}):
            return
        for k, v in value.items():
            low = str(k).lower()
            if isinstance(v, str) and not low.endswith("_env") and any(p in low for p in SECRET_KEY_PARTS):
                raise ValueError(f"설정 {path}.{k}: 비밀 값을 설정 파일에 직접 적을 수 없다. "
                                 f"비밀은 NAME_FILE로 주고 설정에는 {{secret: NAME}}만 쓴다")
            check_inline_secrets(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            check_inline_secrets(v, f"{path}[{i}]")


FOREVER = "forever"
# 출처: D88/U4 최소 보존 잠정 기본값(원본 30일, 사례 180일, 기록 1년). docs/adr/0007-retention-defaults.md
# failed_raw_days(재시도 대상 DEID_FAILED·PUBLISH_FAILED 원본의 기한)는 기본값이 없다: 적지 않으면 raw_days를 따른다(raw_days가 forever면 forever).
# 근거 없는 별도 숫자를 채우면 운영자가 정한 raw_days보다 먼저(또는 forever인데도) 재처리 대상이 지워진다(점검 round3 M1). docs/adr/0007.
RETENTION_DEFAULTS = {"raw_days": 30, "cases_days": 180, "metrics_days": 365}


def _brief(e) -> str:
    # 왜: 위반 값이 비밀일 수 있어 경로와 규칙 종류만 낸다(값은 싣지 않는다).
    return f"{'.'.join(map(str, e.absolute_path)) or '<root>'}: {e.validator}"


def load_config(path: str | Path) -> dict:
    cfg = yaml.safe_load(Path(path).read_text("utf-8"))
    if not isinstance(cfg, dict):
        raise ValueError("설정 파일이 객체가 아니다")
    check_inline_secrets(cfg)
    # 키·형식·범위는 계약 스키마가 정한다(OpsFeedbackJobConfig, 모르는 키는 어디서나 거부). 손으로 같은 검사를 되풀이하지 않는다(R7).
    errs = sorted(validator("OpsFeedbackJobConfig").iter_errors(cfg), key=lambda e: list(map(str, e.absolute_path)))
    if errs:
        raise ValueError("설정이 OpsFeedbackJobConfig 스키마를 어긴다: " + "; ".join(_brief(e) for e in errs[:5]))
    ret = cfg["retention"]
    # D88/U4 최소 보존 기본값: 처리 끝난 원본 30일, 비식별 실패 사례 180일, 기록 1년. 키를 생략하면 기본값(null은 스키마가 거부한다: 기본 무기한 금지).
    # 무기한이 정말 필요하면 명시적으로 "forever"를 쓴다(시작 때마다 경고).
    for key in RETENTION_DEFAULTS:
        ret.setdefault(key, RETENTION_DEFAULTS[key])
    return cfg


def validate_server_env(environ) -> None:
    """서버 환경 변수 중 OPS_ 접두 변수를 OpsFeedbackServerEnv로 검증한다(모르는 이름은 거부: 오타로 보관 경고·검토자 신원 필수가 조용히 꺼지는 것을 막는다).
    범위(0 초과 3600 이하 등)는 스키마가 모양만 보므로 각 읽는 곳이 검사한다."""
    env = {k: v for k, v in environ.items() if k.startswith("OPS_")}
    errs = list(validator("OpsFeedbackServerEnv").iter_errors({"env": env}))
    if errs:
        bad = sorted(k for k in env if not validator("OpsFeedbackServerEnv").is_valid({"env": {k: env[k]}}))
        # 값은 비밀일 수 있어 변수 이름만 낸다
        raise ValueError("환경 변수가 OpsFeedbackServerEnv 스키마를 어긴다: " + ", ".join(bad or [_brief(e) for e in errs[:3]]))

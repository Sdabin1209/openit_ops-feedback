"""설정 파일 로더. 비식별 기준, 보관 기간, 분류체계, 롤백 기준은 사용자가 정하므로 코드에 기본값이 없다."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

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


REQUIRED_TOP = ("storage", "deid", "detection", "classification", "retention")


def load_config(path: str | Path) -> dict:
    cfg = yaml.safe_load(Path(path).read_text("utf-8"))
    if not isinstance(cfg, dict):
        raise ValueError("설정 파일이 객체가 아니다")
    check_inline_secrets(cfg)
    for k in REQUIRED_TOP:
        if k not in cfg:
            raise ValueError(f"설정에 {k} 항목이 없다")
    st = cfg["storage"]
    for k in ("log_db", "failure_db"):
        if k not in st:
            raise ValueError(f"storage.{k}가 없다")
    if "raw_days" not in cfg["retention"]:
        raise ValueError("retention.raw_days가 없다. 보관 기간은 사용자가 정한다. 정하기 전이면 null로 두면 삭제하지 않는다")
    rb = cfg.get("rollback")
    if rb:
        for k in ("window_hours", "min_turns", "max_failure_rate", "counted_review_statuses", "requested_by_role", "auto_submit"):
            if k not in rb:
                raise ValueError(f"rollback.{k}가 없다. 롤백 기준은 사용자가 정한다")
    return cfg

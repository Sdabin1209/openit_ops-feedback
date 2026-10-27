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


FOREVER = "forever"
# 출처: D88/U4 최소 보존 잠정 기본값(원본 30일, 사례 180일, 기록 1년). docs/adr/0007-retention-defaults.md
RETENTION_DEFAULTS = {"raw_days": 30, "cases_days": 180, "metrics_days": 365}
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
    ret = cfg["retention"]
    if not isinstance(ret, dict):
        raise ValueError("retention은 객체")
    # D88/U4 최소 보존 기본값: 처리 끝난 원본 30일, 비식별 실패 사례 180일, 지표·롤백 기록 365일. 키를 생략하면 기본값, null은 거부한다
    # (기본 무기한 금지). 무기한이 정말 필요하면 명시적으로 "forever"를 쓴다(시작 때마다 경고).
    for key, default in RETENTION_DEFAULTS.items():
        if key not in ret:
            ret[key] = default
        v = ret[key]
        if v == FOREVER:
            continue
        if v is None:
            raise ValueError(f"retention.{key}가 null이다. 기본 무기한은 허용하지 않는다: 일 수를 적거나 명시적으로 \"forever\"를 쓴다")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            raise ValueError(f"retention.{key}는 0보다 큰 일 수 또는 \"forever\"")
    if ret.get("raw_after_processing", "keep") not in ("keep", "minimize"):
        raise ValueError("retention.raw_after_processing은 keep 또는 minimize")
    for flag in ("purge_unprocessed",):
        if flag in ret and not isinstance(ret[flag], bool):
            raise ValueError(f"retention.{flag}는 true 또는 false(문자열 불가)")
    bs = (cfg.get("job") or {}).get("batch_size", 1)
    if isinstance(bs, bool) or not isinstance(bs, int) or bs < 1:
        raise ValueError("job.batch_size는 1 이상의 정수")
    rb = cfg.get("rollback")
    if rb:
        for k in ("window_hours", "min_turns", "max_failure_rate", "counted_review_statuses", "requested_by_role", "auto_submit"):
            if k not in rb:
                raise ValueError(f"rollback.{k}가 없다. 롤백 기준은 사용자가 정한다")
        if not isinstance(rb["auto_submit"], bool):      # 문자열 "false"는 참으로 평가되어 실수로 자동 전송된다
            raise ValueError("rollback.auto_submit은 true 또는 false(YAML 불리언)여야 한다. 문자열은 거부한다")
    return cfg

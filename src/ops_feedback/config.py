"""설정 파일 로더. 비식별 기준, 보관 기간, 분류체계, 롤백 기준은 사용자가 정하므로 코드에 기본값이 없다."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REQUIRED_TOP = ("storage", "deid", "detection", "classification", "retention")


def load_config(path: str | Path) -> dict:
    cfg = yaml.safe_load(Path(path).read_text("utf-8"))
    if not isinstance(cfg, dict):
        raise ValueError("설정 파일이 객체가 아니다")
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

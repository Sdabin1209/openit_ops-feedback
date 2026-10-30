"""B6(표준성 점검 2026-10-29): salt는 32바이트 이상이어야 기동한다(ADR 0017). 값은 어떤 오류에도 싣지 않는다."""
import json
import os
import subprocess
import sys

import pytest
import yaml

from conftest import base_config, envelope, pii_trace
from ops_feedback.wiring.build import build_from_config
from ops_feedback.wiring.log_store import LogStore

OK32 = "k" * 32
SHORT31 = "S31-canary-" + "z" * 20          # 31바이트


def _cfg(tmp_path):
    cfg = base_config()
    cfg["storage"] = {"log_db": str(tmp_path / "log.db"), "failure_db": str(tmp_path / "f.db")}
    p = tmp_path / "job.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return cfg, p


def _env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPS_")}
    env.update(extra)
    return env


def test_31_bytes_rejected_32_accepted_in_job_build(tmp_path):
    cfg, _ = _cfg(tmp_path)
    assert len(SHORT31.encode()) == 31
    with pytest.raises(ValueError) as e:
        build_from_config(cfg, {"OPS_DEID_SALT": SHORT31})
    assert SHORT31 not in str(e.value) and "32" in str(e.value)
    build_from_config(cfg, {"OPS_DEID_SALT": OK32})


def test_length_is_counted_in_bytes_not_characters(tmp_path):
    cfg, _ = _cfg(tmp_path)
    korean = "가" * 11            # 11자지만 UTF-8 33바이트
    assert len(korean) == 11 and len(korean.encode()) == 33
    build_from_config(cfg, {"OPS_DEID_SALT": korean})
    with pytest.raises(ValueError):
        build_from_config(cfg, {"OPS_DEID_SALT": "가" * 10})      # 30바이트


def test_job_cli_refuses_short_salt_with_exit_2_and_no_value(tmp_path):
    _, p = _cfg(tmp_path)
    r = subprocess.run([sys.executable, "-m", "ops_feedback.job", "--config", str(p)],
                       env=_env(OPS_DEID_SALT=SHORT31), capture_output=True, text=True)
    assert r.returncode != 0 and SHORT31 not in r.stdout + r.stderr


def test_admin_delete_session_refuses_short_salt(tmp_path):
    ldb, fdb = str(tmp_path / "l.db"), str(tmp_path / "f.db")
    LogStore.sqlite(ldb)
    base = [sys.executable, "-m", "ops_feedback.admin", "--log-db", ldb, "--failure-db", fdb]
    r = subprocess.run(base + ["delete-session", "--session-id", "s-a"], env=_env(OPS_DEID_SALT=SHORT31), capture_output=True, text=True)
    assert r.returncode == 2 and SHORT31 not in r.stdout + r.stderr
    r = subprocess.run(base + ["delete-session", "--session-id", "s-a"], env=_env(OPS_DEID_SALT=OK32), capture_output=True, text=True)
    assert r.returncode == 3          # 길이는 통과하고 지울 것이 없어 3

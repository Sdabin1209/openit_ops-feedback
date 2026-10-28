"""명령줄 잡을 SQLite 파일로 끝까지 실행한다."""
import json
import os
import subprocess
import sys

import yaml

from conftest import envelope, make_trace, pii_trace, base_config
from ops_feedback.logstore import LogStore


def test_cli_run_once_twice(tmp_path):
    cfg = base_config()
    cfg["storage"] = {"log_db": str(tmp_path / "log.db"), "failure_db": str(tmp_path / "f.db")}
    cp = tmp_path / "job.yaml"
    cp.write_text(yaml.safe_dump(cfg))
    log = LogStore.sqlite(cfg["storage"]["log_db"])
    log.send(envelope(pii_trace("s-1", "t-1")))
    log.send(envelope(make_trace("s-1", "t-2")))
    env = {**os.environ, "OPS_DEID_SALT": "test-salt-0123456789abcdef-0123456789abcdef"}
    def go(*extra):
        r = subprocess.run([sys.executable, "-m", "ops_feedback.job", "--config", str(cp), *extra], env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)
    a = go()
    assert a["cases_published"] == 1 and a["no_failure"] == 1
    b = go("--reprocess")
    assert b["cases_published"] == 0 and b["cases_existing"] == 1


def test_cli_refuses_without_salt(tmp_path):
    cfg = base_config()
    cfg["storage"] = {"log_db": str(tmp_path / "log.db"), "failure_db": str(tmp_path / "f.db")}
    cp = tmp_path / "job.yaml"
    cp.write_text(yaml.safe_dump(cfg))
    env = {k: v for k, v in os.environ.items() if k != "OPS_DEID_SALT"}
    r = subprocess.run([sys.executable, "-m", "ops_feedback.job", "--config", str(cp)], env=env, capture_output=True, text=True)
    assert r.returncode != 0

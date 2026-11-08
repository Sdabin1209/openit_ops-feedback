"""명령줄 잡을 SQLite 파일로 끝까지 실행한다."""
import json
import os
import subprocess
import sys

import yaml

from conftest import envelope, make_trace, pii_trace, base_config
from ops_feedback.wiring.log_store import LogStore
from minenv import clean_env


def test_cli_run_once_twice(tmp_path):
    cfg = base_config()
    cfg["storage"] = {"log_db": str(tmp_path / "log.db"), "failure_db": str(tmp_path / "f.db")}
    cp = tmp_path / "job.yaml"
    cp.write_text(yaml.safe_dump(cfg))
    log = LogStore.sqlite(cfg["storage"]["log_db"])
    log.send(envelope(pii_trace("s-1", "t-1")))
    log.send(envelope(make_trace("s-1", "t-2")))
    env = clean_env({"OPS_DEID_SALT": "test-salt-0123456789abcdef-0123456789abcdef"})
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
    env = clean_env()
    r = subprocess.run([sys.executable, "-m", "ops_feedback.job", "--config", str(cp)], env=env, capture_output=True, text=True)
    assert r.returncode != 0


def test_job_and_admin_refuse_unknown_ops_env(tmp_path):
    """OpsFeedbackServerEnv: 모르는 OPS_ 변수(오타)는 서버뿐 아니라 잡과 admin도 시작을 거부한다. 오류에는 변수 이름만 나오고 값은 나오지 않는다."""
    cfg = base_config()
    cfg["storage"] = {"log_db": str(tmp_path / "log.db"), "failure_db": str(tmp_path / "f.db")}
    cp = tmp_path / "job.yaml"
    cp.write_text(yaml.safe_dump(cfg))
    env = clean_env({"OPS_DEID_SALT": "test-salt-0123456789abcdef-0123456789abcdef", "OPS_DB_SCEMA": "secret-value-xyz"})
    for argv in ([sys.executable, "-m", "ops_feedback.job", "--config", str(cp)],
                 [sys.executable, "-m", "ops_feedback.admin", "--log-db", str(tmp_path / "l.db"), "--failure-db", str(tmp_path / "f2.db"), "migrate"]):
        r = subprocess.run(argv, env=env, capture_output=True, text=True)
        assert r.returncode == 2 and "OPS_DB_SCEMA" in r.stderr and "secret-value-xyz" not in r.stderr + r.stdout, (argv[2], r.stderr)
    assert not (tmp_path / "log.db").exists()          # 거부는 저장소를 만들기 전이다

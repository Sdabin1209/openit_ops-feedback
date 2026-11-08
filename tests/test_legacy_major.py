"""계약 5.0.0 '이전'(헤드 결정 2026-11-08): 4.x 저장 데이터는 이전하지 않고, 남아 있으면 서버·잡이 조용히 섞지 않고 시작을 거부한다(UNSUPPORTED_VERSION).
admin(삭제·purge)은 같은 저장소를 열 수 있어야 남은 데이터를 지운다."""
import json
import os
import sqlite3
import subprocess
import sys

import pytest
import yaml
from boundary_contracts.errors import BoundaryError

from conftest import base_config, envelope, make_trace
from ops_feedback.components.failure_case_store import FailureCaseStore
from ops_feedback.wiring.log_store import LogStore
from ops_feedback.wiring.storage import require_current_major
from minenv import clean_env
from waiting import HANG_GUARD_SECONDS

SALT = "test-salt-0123456789abcdef-0123456789abcdef"


def seed_legacy_log(path):
    log = LogStore.sqlite(path)
    log.send(envelope(make_trace("s-1", "t-1")))
    db = sqlite3.connect(path)
    n = db.execute("UPDATE raw_traces SET envelope = REPLACE(envelope, '\"pipeline_schema_version\": \"5.0\"', '\"pipeline_schema_version\": \"4.0\"')").rowcount
    db.commit()
    db.close()
    assert n == 1
    return log


def seed_failure(path, major):
    FailureCaseStore.sqlite(path)
    rec = {"case_id": "fc-x", "version": 1, "pipeline_schema_version": f"{major}.0"}
    db = sqlite3.connect(path)
    db.execute("INSERT INTO cases(case_id,version,review_status,failure_type,source_release_id,created_at,record) VALUES('fc-x',1,'PENDING','T','rel-1','2026-11-08T00:00:00Z',?)",
               (json.dumps(rec, ensure_ascii=False),))
    db.commit()
    db.close()
    return FailureCaseStore.sqlite(path)


def test_clean_stores_start(tmp_path):
    log = LogStore.sqlite(str(tmp_path / "l.db"))
    log.send(envelope(make_trace("s-1", "t-1")))
    require_current_major(log, seed_failure(str(tmp_path / "f.db"), 5))


def test_4x_log_row_refuses_start(tmp_path):
    with pytest.raises(BoundaryError) as e:
        require_current_major(seed_legacy_log(str(tmp_path / "l.db")), None)
    assert e.value.code == "UNSUPPORTED_VERSION"


def test_4x_failure_case_refuses_start(tmp_path):
    with pytest.raises(BoundaryError) as e:
        require_current_major(None, seed_failure(str(tmp_path / "f.db"), 4))
    assert e.value.code == "UNSUPPORTED_VERSION"


def test_job_exits_nonzero_when_4x_data_remains(tmp_path):
    cfg = base_config()
    cfg["storage"] = {"log_db": str(tmp_path / "log.db"), "failure_db": str(tmp_path / "f.db")}
    cp = tmp_path / "job.yaml"
    cp.write_text(yaml.safe_dump(cfg))
    seed_legacy_log(cfg["storage"]["log_db"])
    r = subprocess.run([sys.executable, "-m", "ops_feedback.job", "--config", str(cp)], env=clean_env({"OPS_DEID_SALT": SALT}), capture_output=True, text=True, timeout=HANG_GUARD_SECONDS)      # 매달림 감시
    assert r.returncode == 2 and "UNSUPPORTED_VERSION" in r.stderr
    assert r.stdout == ""


def test_admin_can_still_open_a_store_with_4x_data(tmp_path):
    path = str(tmp_path / "l.db")
    seed_legacy_log(path)
    r = subprocess.run([sys.executable, "-m", "ops_feedback.admin", "--log-db", path, "--failure-db", str(tmp_path / "f.db"), "purge", "--raw-days", "0", "--include-unprocessed"],
                       env=clean_env({"OPS_DEID_SALT": SALT}), capture_output=True, text=True, timeout=HANG_GUARD_SECONDS)      # 매달림 감시
    assert r.returncode == 0, r.stderr


def test_server_exits_nonzero_when_4x_data_remains(tmp_path):
    path = str(tmp_path / "l.db")
    seed_legacy_log(path)
    env = clean_env({"OPS_LOG_DB": path, "OPS_INGEST_TOKEN": "t" * 40, "OPS_DEID_SALT": SALT})
    r = subprocess.run([sys.executable, "-m", "ops_feedback.api"], env=env, capture_output=True, text=True, timeout=HANG_GUARD_SECONDS)      # 매달림 감시
    assert r.returncode == 2 and "4.x" in (r.stderr + r.stdout)

"""scripts/setup-env.sh의 표준 출력 마지막 줄은 계약 SetupEnvReport 한 줄이다(계약 2.6.0, OPEN_GAPS U43).
스크립트를 임시 폴더에 복사하고 pip를 가짜로 바꿔 실행한다(실제 설치는 하지 않는다: 시험이 쓰는 .venv를 건드리면 안 된다)."""
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from boundary_contracts import validator

ROOT = Path(__file__).resolve().parents[1]


def sandbox(tmp_path: Path, pip_exit: int) -> Path:
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "setup-env.sh", tmp_path / "scripts" / "setup-env.sh")
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    pip = tmp_path / ".venv" / "bin" / "pip"
    pip.write_text(f"#!/usr/bin/env bash\necho 'pip noise on stdout: $*'\nexit {pip_exit}\n")
    pip.chmod(pip.stat().st_mode | stat.S_IXUSR)
    return tmp_path


def run(tmp_path, pip_exit):
    d = sandbox(tmp_path, pip_exit)
    return subprocess.run(["bash", "scripts/setup-env.sh"], cwd=d, capture_output=True, text=True,
                          env={**os.environ, "SETUP_ENV_RETRY_WAIT": "0"})


def test_last_stdout_line_is_a_valid_setup_env_report(tmp_path):
    r = run(tmp_path, 0)
    assert r.returncode == 0, r.stderr
    last = r.stdout.strip().splitlines()[-1]
    rep = json.loads(last)
    assert not list(validator("SetupEnvReport").iter_errors(rep)), rep
    assert rep["area"] == "ops-feedback" and rep["status"] == "ok" and rep["exit_code"] == 0
    assert rep["environment"] == {"kind": "python-venv", "path": ".venv", "test_command": [".venv/bin/python", "-m", "pytest", "-q"]}
    assert "pip noise" not in r.stdout          # 진행 기록(pip 출력)은 표준 오류로


def test_failure_still_ends_with_a_report_and_nonzero_exit(tmp_path):
    r = run(tmp_path, 1)
    assert r.returncode == 1
    rep = json.loads(r.stdout.strip().splitlines()[-1])
    assert not list(validator("SetupEnvReport").iter_errors(rep))
    assert rep["status"] == "fail" and rep["exit_code"] == 1


def test_reported_test_command_starts_the_area_tests(tmp_path):
    """보고의 test_command를 리포 루트에서 실행하면 영역 시험이 돈다(전체를 되돌아 돌리지 않으려고 한 파일만 수집해 시작 가능성만 본다)."""
    cmd = json.loads(run(tmp_path, 0).stdout.strip().splitlines()[-1])["environment"]["test_command"]
    r = subprocess.run([*cmd, "--collect-only", "tests/test_setup_env.py"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0 and "test_setup_env.py" in r.stdout, r.stdout[-300:] + r.stderr[-300:]

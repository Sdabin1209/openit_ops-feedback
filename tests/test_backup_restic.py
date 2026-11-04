"""실제 restic + 표준 restic rest-server(컨테이너)로 백업 -> 스냅숏 -> 복구 -> 복구한 DB 에서 행 확인(RUNTIME R10.1, ADR 0032).
이 시험이 띄우는 것: rest-server 컨테이너 하나(이름 ops-feedback-restsrv-<무작위>)와 restic 바이너리를 꺼내려고 잠깐 만드는 컨테이너 하나. 둘 다 이 시험이 만든 이름으로만 지운다.
PostgreSQL(TEST_DATABASE_URL)과 docker 가 없으면 건너뛴다(PASS 로 세지 않는다)."""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import uuid

import pytest

from boundary_contracts import validator
from boundary_contracts.errors import BoundaryError
from ops_feedback.admin import __main__ as admin_cli
from conftest import _pg_schema_url, envelope, make_trace
from ops_feedback.components.failure_case_store import FailureCaseStore
from ops_feedback.wiring import backup_flow
from ops_feedback.wiring.log_store import LogStore
from waiting import HANG_GUARD_SECONDS, Proc

USER, PW = "backupuser", "pw-restic-canary-5"


def _docker(*a, check=True):
    return subprocess.run(["docker", *a], capture_output=True, text=True, check=check, timeout=HANG_GUARD_SECONDS)


@pytest.fixture(scope="module")
def restic_bin(tmp_path_factory):
    if shutil.which("docker") is None or _docker("info", check=False).returncode != 0:
        pytest.skip("docker 없음")
    d = tmp_path_factory.mktemp("restic")
    name = f"ops-feedback-restic-extract-{uuid.uuid4().hex[:8]}"
    _docker("create", "--name", name, "restic/restic:latest")
    try:
        _docker("cp", f"{name}:/usr/bin/restic", str(d / "restic"))
    finally:
        _docker("rm", "-f", name, check=False)
    (d / "restic").chmod(0o755)
    return str(d / "restic")


@pytest.fixture
def rest_server(tmp_path):
    data = tmp_path / "repo"
    data.mkdir()
    data.chmod(0o777)
    (data / ".htpasswd").write_text(f"{USER}:{{SHA}}{base64.b64encode(hashlib.sha1(PW.encode()).digest()).decode()}\n")      # rest-server 의 htpasswd 인증
    (data / ".htpasswd").chmod(0o644)
    name = f"ops-feedback-restsrv-{uuid.uuid4().hex[:8]}"
    _docker("run", "-d", "--name", name, "-p", "127.0.0.1::8000", "-v", f"{data}:/data", "--user", f"{os.getuid()}:{os.getgid()}", "--entrypoint", "rest-server", "restic/rest-server:latest", "--path", "/data")
    logs = Proc(["docker", "logs", "-f", name], dict(os.environ))
    try:
        logs.wait_for("Authentication enabled")                # 서버가 인증을 켜고 기동했다는 사건
        port = _docker("port", name, "8000/tcp").stdout.split(":")[-1].strip()
        yield f"http://127.0.0.1:{port}/ops-feedback"           # 평문 http 는 loopback 에서만 허용된다(R1.1)
    finally:
        _docker("rm", "-f", name, check=False)
        logs.p.kill()
    # 컨테이너는 현재 사용자로 돌므로 저장소 아래 모든 파일의 소유자도 현재 사용자다. root 소유 파일이 남으면 pytest 가 임시 폴더를 지우지 못하고 /tmp 에 쌓인다.
    foreign = [str(p) for p in [data, *data.rglob("*")] if p.lstat().st_uid != os.getuid()]
    if foreign:
        _docker("run", "--rm", "-v", f"{data}:/d", "--entrypoint", "sh", "restic/rest-server:latest", "-c", "chown -R %d:%d /d" % (os.getuid(), os.getgid()), check=False)     # 남기지 않는다(이 시험이 만든 폴더만)
    assert not foreign, f"root 소유 파일이 남았다: {len(foreign)}개"


def test_real_backup_snapshot_restore_and_rows(request, tmp_path, restic_bin, rest_server, monkeypatch, capsys):
    import psycopg
    log_url, fail_url = _pg_schema_url(request), _pg_schema_url(request)
    store = LogStore.postgres(log_url)
    FailureCaseStore.postgres(fail_url)._b.close()
    for i in range(3):
        store.send(envelope(make_trace("s-real", f"t-{i}")))
    (tmp_path / "cred").write_text(f"{USER}:{PW}\n")
    (tmp_path / "rp").write_text("repo-password-real\n")
    (tmp_path / "lu").write_text(log_url)
    (tmp_path / "fu").write_text(fail_url)
    env = {"OPENIT_BACKUP_URL": rest_server, "OPENIT_BACKUP_CREDENTIALS_FILE": str(tmp_path / "cred"), "OPENIT_BACKUP_REPO_PASSWORD_FILE": str(tmp_path / "rp"),
           "OPS_LOG_DATABASE_URL_FILE": str(tmp_path / "lu"), "OPS_FAILURE_DATABASE_URL_FILE": str(tmp_path / "fu"), "OPS_RAW_RETENTION_DAYS": "30"}
    res = backup_flow.run(env, restic=restic_bin)
    assert res["status"] == "ok" and res["bytes"] > 0 and res["snapshot_id"]
    # 인증은 실제로 켜져 있다: 틀린 자격이면 백업이 UNAVAILABLE(재시도 가능)로 실패하고 암호가 메시지에 없다
    (tmp_path / "cred2").write_text(f"{USER}:wrong\n")
    with pytest.raises(BoundaryError) as e:
        backup_flow.run(env | {"OPENIT_BACKUP_CREDENTIALS_FILE": str(tmp_path / "cred2")}, restic=restic_bin)
    assert e.value.code == "UNAVAILABLE" and PW not in e.value.message
    # 같은 잡을 다시 돌려도 안전하다(멱등: 스냅숏이 하나 더 생길 뿐 실패하지 않는다)
    second = backup_flow.run(env, restic=restic_bin)
    assert second["status"] == "ok" and second["snapshot_id"] != res["snapshot_id"]

    # 복구 잡(admin restore): 스냅숏 ID 로 새 빈 DB 에 되돌린다. 대상은 migrate 로 스키마만 만든 DB
    def fresh():
        a, b = _pg_schema_url(request), _pg_schema_url(request)
        LogStore.postgres(a)._raw._backend.close()
        FailureCaseStore.postgres(b)._b.close()
        return a, b

    def renv(a, b):
        (tmp_path / "ru").write_text(a)
        (tmp_path / "rf").write_text(b)
        return env | {"OPS_LOG_DATABASE_URL_FILE": str(tmp_path / "ru"), "OPS_FAILURE_DATABASE_URL_FILE": str(tmp_path / "rf")}

    new_log, new_fail = fresh()
    back = backup_flow.restore(renv(new_log, new_fail), res["snapshot_id"], restic=restic_bin)
    assert back["status"] == "ok" and back["snapshot_id"] == res["snapshot_id"] and not list(validator("BackupJobResult").iter_errors(back))
    with psycopg.connect(new_log) as c:
        assert c.execute("SELECT count(*) FROM raw_traces").fetchone()[0] == 3
        assert {r[0] for r in c.execute("SELECT turn_id FROM raw_traces").fetchall()} == {"t-0", "t-1", "t-2"}
    # 복구한 DB 는 비어 있지 않으므로 같은 대상에 다시 복구하면 거부한다(덮어쓰지 않는다): FAILED_PRECONDITION, 종료 코드 1
    with pytest.raises(BoundaryError) as e:
        backup_flow.restore(renv(new_log, new_fail), "latest", restic=restic_bin)
    assert e.value.code == "FAILED_PRECONDITION"
    # latest 는 또 다른 빈 DB 에 되돌리고, CLI 의 마지막 줄 JSON 과 종료 코드 0 을 낸다
    l2, f2 = fresh()
    for k, v in renv(l2, f2).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PATH", f"{os.path.dirname(restic_bin)}:{os.environ['PATH']}")
    assert admin_cli.main(["restore"]) == 0
    last = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert last["status"] == "ok" and last["snapshot_id"] == second["snapshot_id"]            # latest = 가장 최근 스냅숏
    with psycopg.connect(l2) as c:
        assert c.execute("SELECT count(*) FROM raw_traces").fetchone()[0] == 3

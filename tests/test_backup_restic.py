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
    _docker("run", "-d", "--name", name, "-p", "127.0.0.1::8000", "-v", f"{data}:/data", "--user", "0", "--entrypoint", "rest-server", "restic/rest-server:latest", "--path", "/data")
    logs = Proc(["docker", "logs", "-f", name], dict(os.environ))
    try:
        logs.wait_for("Authentication enabled")                # 서버가 인증을 켜고 기동했다는 사건
        port = _docker("port", name, "8000/tcp").stdout.split(":")[-1].strip()
        yield f"http://127.0.0.1:{port}/ops-feedback"           # 평문 http 는 loopback 에서만 허용된다(R1.1)
    finally:
        _docker("rm", "-f", name, check=False)
        logs.p.kill()


def test_real_backup_snapshot_restore_and_rows(request, tmp_path, restic_bin, rest_server, monkeypatch):
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
    from boundary_contracts.errors import BoundaryError
    with pytest.raises(BoundaryError) as e:
        backup_flow.run(env | {"OPENIT_BACKUP_CREDENTIALS_FILE": str(tmp_path / "cred2")}, restic=restic_bin)
    assert e.value.code == "UNAVAILABLE" and PW not in e.value.message
    # 같은 잡을 다시 돌려도 안전하다(멱등: 스냅숏이 하나 더 생길 뿐 실패하지 않는다)
    assert backup_flow.run(env, restic=restic_bin)["status"] == "ok"

    # 복구: 표준 restic restore 로 스냅숏에서 덤프 파일을 꺼낸다
    renv = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "RESTIC_PASSWORD_FILE": str(tmp_path / "rp"),
            "RESTIC_REPOSITORY": "rest:" + rest_server.replace("http://", f"http://{USER}:{PW}@")}
    out = tmp_path / "restored"
    r = subprocess.run([restic_bin, "restore", res["snapshot_id"], "--target", str(out)], env=renv, capture_output=True, text=True, timeout=HANG_GUARD_SECONDS)
    assert r.returncode == 0, r.stderr[-300:].replace(PW, "***")
    dump = (out / "ops-feedback.copy").read_text()
    assert "-- store: log" in dump and "-- store: failure" in dump
    assert "COPY raw_traces FROM STDIN;" in dump and all(f"COPY {t} FROM STDIN;" in dump for t in ("cases", "traces", "aux"))      # 두 저장소의 표가 모두 들어 있다

    # 복구한 데이터를 새 빈 DB(스키마만 migrate 된)에 되돌리고 행을 확인한다
    new_log, new_fail = _pg_schema_url(request), _pg_schema_url(request)
    LogStore.postgres(new_log)._raw._backend.close()
    FailureCaseStore.postgres(new_fail)._b.close()
    sections, cur = {}, None
    for line in dump.splitlines(keepends=True):
        if line.startswith("-- store: "):
            cur = line.split(": ")[1].strip()
            sections[cur] = []
        elif cur:
            sections[cur].append(line)
    for name, url in (("log", new_log), ("failure", new_fail)):
        with psycopg.connect(url, autocommit=True) as c:
            block, table = [], None
            for line in sections[name] + ["\\.\n"]:
                if line.startswith("COPY "):
                    table, block = line.split()[1], []
                elif line == "\\.\n" and table:
                    with c.cursor().copy(f"COPY {table} FROM STDIN") as cp:
                        cp.write("".join(block))
                    table = None
                elif table:
                    block.append(line)
    with psycopg.connect(new_log) as c:
        assert c.execute("SELECT count(*) FROM raw_traces").fetchone()[0] == 3
        assert {r[0] for r in c.execute("SELECT turn_id FROM raw_traces").fetchall()} == {"t-0", "t-1", "t-2"}

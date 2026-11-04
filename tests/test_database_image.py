"""ops-feedback-db 이미지(D126, ADR 0035): 실제로 띄워 선언한 계정이 verify-full 로 접속되고, 권한·접속 규칙이 선언대로인지 본다.
이 시험이 만드는 것: 이미지 하나와 컨테이너 하나(이름 ops-feedback-db-test-<무작위>), 임시 인증기관·서버 인증서·비밀 파일(tmp_path). 정리는 이 이름으로만 한다. docker·openssl 이 없으면 건너뛴다."""
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from boundary_contracts import validator

import deps
from waiting import HANG_GUARD_SECONDS, Proc

ROOT = Path(__file__).resolve().parents[1]
DECL = json.loads((ROOT / "db" / "database.json").read_text("utf-8"))
ACCOUNTS = {a["name"]: a for a in DECL["accounts"]}


def docker(*a, check=True, **kw):
    return subprocess.run(["docker", *a], capture_output=True, text=True, check=check, timeout=HANG_GUARD_SECONDS, **kw)


@pytest.fixture(scope="module")
def image():
    if shutil.which("docker") is None or shutil.which("openssl") is None or docker("info", check=False).returncode != 0:
        pytest.skip("docker 또는 openssl 없음")
    tag = f"ops-feedback-db-test:{uuid.uuid4().hex[:8]}"
    docker("build", "-q", "-t", tag, "--build-arg", "COMMIT=abcdef1", "--build-arg", f"CONTRACTS_VERSION={deps.pinned_boundary_tag()}",
           "--build-arg", "DATABASE_DECLARATION=" + json.dumps(DECL, separators=(",", ":"), sort_keys=True), str(ROOT / "db"))
    yield tag
    docker("rmi", "-f", tag, check=False)


def make_certs(d: Path) -> Path:
    c = d / "certs"
    c.mkdir()

    def sh(*a):
        subprocess.run(a, cwd=c, check=True, capture_output=True, timeout=HANG_GUARD_SECONDS)
    sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.pem", "-days", "1", "-subj", "/CN=test-root")
    sh("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "tls.key", "-out", "srv.csr", "-subj", "/CN=localhost")
    (c / "ext.cnf").write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\n")
    sh("openssl", "x509", "-req", "-in", "srv.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial", "-out", "tls.crt", "-days", "1", "-extfile", "ext.cnf")
    for f in c.iterdir():
        f.chmod(0o644)
    return c


class Db:
    def __init__(self, name, port, certs, passwords):
        self.name, self.port, self.certs, self.pw = name, port, certs, passwords

    def url(self, account, db=None, sslmode="verify-full"):
        db = db or ACCOUNTS[account]["database"]
        q = f"sslmode={sslmode}" + (f"&sslrootcert={self.certs / 'ca.pem'}" if sslmode == "verify-full" else "")
        return f"postgresql://{account}:{self.pw[account]}@localhost:{self.port}/{db}?{q}&hostaddr=127.0.0.1"

    def exec(self, *argv, user="root"):
        return docker("exec", "-u", user, self.name, *argv, check=False)


def start(tmp_path, image, with_certs=True, passwords_override=None):
    certs = make_certs(tmp_path)
    sec = tmp_path / "secrets"
    sec.mkdir()
    pw = {a: uuid.uuid4().hex for a in ACCOUNTS}
    for a, v in pw.items():
        (sec / a).write_text(v + "\n")
        (sec / a).chmod(0o644)
    su = tmp_path / "su"
    su.mkdir()
    (su / "pw").write_text(uuid.uuid4().hex + "\n")
    (su / "pw").chmod(0o644)
    name = f"ops-feedback-db-test-{uuid.uuid4().hex[:8]}"
    args = ["run", "-d", "--name", name, "--health-interval=2s", "--health-retries=2", "--health-start-period=0s", "-p", "127.0.0.1::5432", "-v", f"{sec}:/run/secrets/db:ro", "-v", f"{su}:/run/su:ro", "-e", "POSTGRES_PASSWORD_FILE=/run/su/pw"]
    if with_certs:
        args += ["-v", f"{certs}:/certs:ro"]
    docker(*args, image)
    return name, certs, pw


def wait_ready(name):
    """초기화가 끝난 뒤 본 서버가 뜬 사건(로그 줄)을 기다린다."""
    p = Proc(["docker", "logs", "-f", name], dict(os.environ))
    try:
        with p._cond:
            ok = p._cond.wait_for(lambda: p.eof.is_set() or _ready(p.lines), HANG_GUARD_SECONDS)      # 매달림 감시
        assert ok and _ready(p.lines), "DB 가 뜨지 않았다:\n" + "".join(p.lines)[-800:]
    finally:
        p.p.kill()


def _ready(lines):
    done = [i for i, ln in enumerate(lines) if "init process complete" in ln]
    return bool(done) and any("ready to accept connections" in ln for ln in lines[done[0]:])


@pytest.fixture(scope="module")
def db(image, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("dbimg")
    name, certs, pw = start(tmp, image)
    try:
        wait_ready(name)
        port = docker("port", name, "5432/tcp").stdout.split(":")[-1].strip()
        yield Db(name, port, certs, pw)
    finally:
        docker("rm", "-fv", name, check=False)


def test_label_is_the_declaration_and_names_are_the_servers_env_names(image):
    labels = json.loads(docker("image", "inspect", image, "--format", "{{json .Config.Labels}}").stdout)
    decl = json.loads(labels["io.openit.database"])
    assert decl == DECL and not list(validator("DatabaseDeclaration").iter_errors(decl))
    env = validator("OpsFeedbackServerEnv")
    for a in decl["accounts"]:
        for u in a["url_env"]:
            assert env.is_valid({"env": {u: "/run/secrets/x"}}), u          # 영역 Env 스키마에 있는 *_DATABASE_URL_FILE 이름만 쓴다
    assert {a["privilege"] for a in decl["accounts"]} == {"owner", "dml"} and decl["major"] == 16 and labels["io.openit.service"] == "ops-feedback-db"


def test_every_declared_account_connects_with_verify_full_over_tls(db):
    import psycopg
    for a in ACCOUNTS:
        with psycopg.connect(db.url(a)) as c:
            assert c.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()").fetchone()[0] is True
            assert c.execute("SELECT current_user, current_database()").fetchone() == (a, ACCOUNTS[a]["database"])


def test_plain_text_and_wrong_password_are_refused(db):
    import psycopg
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(db.url("ops_log_app", sslmode="disable"))              # TLS 필수: 평문 접속은 hostnossl reject
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(db.url("ops_log_app").replace(db.pw["ops_log_app"], "wrong"))


def test_accounts_have_no_extra_privileges_and_connection_limits(db):
    import psycopg
    with psycopg.connect(db.url("ops_log_owner")) as c:
        rows = {r[0]: r[1:] for r in c.execute("SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolcanlogin, rolconnlimit FROM pg_roles WHERE rolname LIKE 'ops\\_%'").fetchall()}
    assert set(rows) == set(ACCOUNTS)
    for a, (sup, cdb, crole, repl, bypass, login, lim) in rows.items():
        assert (sup, cdb, crole, repl, bypass, login) == (False, False, False, False, False, True), a
        assert lim == (4 if ACCOUNTS[a]["privilege"] == "owner" else 20), a


def test_owner_runs_ddl_and_app_account_can_only_use_data(db):
    import psycopg
    owner, app = "ops_log_owner", "ops_log_app"
    with psycopg.connect(db.url(owner), autocommit=True) as o:
        o.execute("CREATE TABLE t_dml (id serial PRIMARY KEY, v text)")            # 소유자: DDL 가능, 표를 만든 직후 dml 계정에 권한이 자동으로 붙는다(기본 권한)
    with psycopg.connect(db.url(app), autocommit=True) as a:
        a.execute("INSERT INTO t_dml (v) VALUES ('x')")                              # 시퀀스 USAGE 도 필요하다(serial)
        assert a.execute("SELECT v FROM t_dml").fetchone()[0] == "x"
        a.execute("UPDATE t_dml SET v = 'y'")
        a.execute("DELETE FROM t_dml")
        for ddl in ("CREATE TABLE should_fail (x int)", "ALTER TABLE t_dml ADD COLUMN z int", "DROP TABLE t_dml", "CREATE SCHEMA s_fail", "TRUNCATE t_dml", "CREATE INDEX i_fail ON t_dml (v)"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                a.execute(ddl)


def test_each_account_reaches_only_its_own_database(db):
    import psycopg
    for who, other in (("ops_log_app", "ops_failure"), ("ops_log_owner", "ops_failure"), ("ops_failure_app", "ops_log"), ("ops_failure_owner", "ops_log"),
                       ("ops_log_app", "postgres"), ("ops_failure_app", "template1")):
        with pytest.raises(psycopg.OperationalError, match="permission denied for database"):
            psycopg.connect(db.url(who, db=other))                                  # PUBLIC CONNECT 회수


def test_no_trust_anywhere_and_local_sockets_are_closed(db):
    # 효력 있는 접속 규칙에 trust 가 없다
    r = db.exec("psql", "-U", "postgres", "-At", "-c", "SELECT string_agg(DISTINCT auth_method, ',') FROM pg_hba_file_rules", user="postgres")
    assert r.returncode == 0 and "trust" not in r.stdout and set(r.stdout.strip().split(",")) == {"peer", "reject", "scram-sha-256"}, r.stdout + r.stderr[-200:]
    # 데이터 폴더의 pg_hba.conf 도 같은 내용이다(initdb 의 trust 줄이 남지 않았다)
    assert "trust" not in db.exec("sh", "-c", "grep -v '^#' $PGDATA/pg_hba.conf").stdout
    # 컨테이너 안 루프백 TCP 로 암호 없이 슈퍼유저 접속: 거부(TLS 없는 접속 reject, 암호 필요)
    assert db.exec("psql", "-h", "127.0.0.1", "-U", "postgres", "-w", "-c", "SELECT 1").returncode != 0
    assert db.exec("psql", "-h", "127.0.0.1", "-U", "ops_log_app", "-d", "ops_log", "-w", "-c", "SELECT 1").returncode != 0
    # 소켓은 OS 사용자 postgres 만(peer). 다른 계정의 소켓 접속은 reject
    assert db.exec("psql", "-U", "ops_log_app", "-d", "ops_log", "-w", "-c", "SELECT 1", user="postgres").returncode != 0


def test_app_code_migrates_with_owner_and_runs_with_app_account_over_verify_full(db, monkeypatch):
    """영역 코드(admin migrate 와 verify 모드 저장소)가 이 DB 에서 선언대로 일한다: 소유자 URL 로 스키마를 만들고, 앱 URL 로 수집한다."""
    import psycopg
    from conftest import envelope, make_trace
    from ops_feedback.admin import __main__ as admin_cli
    from ops_feedback.components.failure_case_store import FailureCaseStore
    from ops_feedback.wiring.log_store import LogStore
    from ops_feedback.wiring.storage import require_verify
    for k in [k for k in os.environ if k.startswith("OPS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("OPS_LOG_DATABASE_URL", db.url("ops_log_owner", db="ops_log"))
    monkeypatch.setenv("OPS_FAILURE_DATABASE_URL", db.url("ops_failure_owner", db="ops_failure"))
    assert admin_cli.main(["migrate"]) == 0
    env = require_verify({})
    log = LogStore.postgres(db.url("ops_log_app"), environ=env)               # 앱 계정: DDL 없이 시작한다(verify)
    FailureCaseStore.postgres(db.url("ops_failure_app"), environ=env)._b.close()
    assert log.send(envelope(make_trace("s-img", "t-1")))["status"] == "ACCEPTED"
    log.ping()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        log._raw._backend._db.execute("DROP TABLE raw_traces")


def test_without_server_certificate_the_container_refuses_to_start(image, tmp_path):
    name, _, _ = start(tmp_path, image, with_certs=False)
    try:
        r = docker("wait", name)
        logs = docker("logs", name, check=False)
        assert r.stdout.strip() != "0" and "서버 TLS 필수" in (logs.stdout + logs.stderr)           # TLS 없이 뜨지 않는다(진입점의 거부 문구)
    finally:
        docker("rm", "-fv", name, check=False)


def test_missing_account_secret_stops_initialisation(image, tmp_path):
    certs = make_certs(tmp_path)
    sec = tmp_path / "secrets"
    sec.mkdir()
    (sec / "ops_log_owner").write_text("pw\n")
    (sec / "ops_log_owner").chmod(0o644)              # 나머지 계정 파일이 없다
    su = tmp_path / "su"
    su.mkdir()
    (su / "pw").write_text("su-pw\n")
    (su / "pw").chmod(0o644)
    name = f"ops-feedback-db-test-{uuid.uuid4().hex[:8]}"
    docker("run", "-d", "--name", name, "-v", f"{sec}:/run/secrets/db:ro", "-v", f"{su}:/run/su:ro", "-v", f"{certs}:/certs:ro", "-e", "POSTGRES_PASSWORD_FILE=/run/su/pw", image)
    try:
        assert docker("wait", name).stdout.strip() != "0"
        logs = docker("logs", name, check=False)
        assert "암호 파일이 없거나 비어 있다" in logs.stdout + logs.stderr
    finally:
        docker("rm", "-fv", name, check=False)


def test_renewed_server_certificate_is_read_again_without_restart(image, tmp_path):
    """인증서 갱신: DB_TLS_DIR 의 파일을 새 인증기관이 서명한 것으로 바꾸면 컨테이너를 다시 띄우지 않아도 서버가 새 인증서를 쓴다(verify-full 이 새 루트로 통과하고 옛 루트로는 실패)."""
    import psycopg
    name, certs, pw = start(tmp_path, image)
    try:
        wait_ready(name)
        port = docker("port", name, "5432/tcp").stdout.split(":")[-1].strip()
        d = Db(name, port, certs, pw)
        with psycopg.connect(d.url("ops_log_app")):
            pass                                                   # 옛 루트로 통과한다
        new = tmp_path / "newcerts"
        new.mkdir()
        sub = make_certs(new)                                      # 다른 인증기관이 서명한 새 인증서
        logs = Proc(["docker", "logs", "-f", "--since", "0s", name], dict(os.environ))
        try:
            for f in ("tls.crt", "tls.key"):
                shutil.copyfile(sub / f, certs / (f + ".tmp"))
                os.replace(certs / (f + ".tmp"), certs / f)       # 원자적 교체(secret 갱신과 같은 방식)
                (certs / f).chmod(0o644)
            logs.wait_for("TLS 인증서를 다시 읽었다")
        finally:
            logs.p.kill()
        new_url = d.url("ops_log_app").replace(str(certs / "ca.pem"), str(sub / "ca.pem"))
        with psycopg.connect(new_url) as c:
            assert c.execute("SELECT 1").fetchone()[0] == 1       # 새 루트로 verify-full 통과
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(d.url("ops_log_app"))                  # 옛 루트로는 더 이상 통과하지 않는다
    finally:
        docker("rm", "-fv", name, check=False)


@pytest.mark.parametrize("breakage", ["tls_crt", "account_secret"])
def test_healthcheck_is_healthy_with_declared_accounts_and_unhealthy_when_broken(image, tmp_path, breakage):
    """HEALTHCHECK(Docker): 선언한 모든 계정이 TLS 로 접속되면 healthy. 서버 인증서 파일이나 계정 암호 파일이 사라지면 unhealthy(변이). 판정은 docker events 의 health_status 사건으로 받는다(시간 대기 없음).
    시험은 확인 주기를 docker run 옵션(--health-*)으로 줄인다(이미지의 기본은 주기 10초)."""
    decl = json.loads(docker("image", "inspect", image, "--format", "{{json .Config.Healthcheck}}").stdout)
    assert decl["Test"][-1] == "ops-feedback-db-healthcheck.sh" and decl["Retries"] == 3
    name, certs, pw = start(tmp_path, image)
    ev = Proc(["docker", "events", "--filter", f"container={name}", "--filter", "event=health_status"], dict(os.environ))
    try:
        ev.wait_for("health_status: healthy")
        assert docker("inspect", name, "--format", "{{.State.Health.Status}}").stdout.strip() == "healthy"
        (certs / "tls.crt" if breakage == "tls_crt" else tmp_path / "secrets" / "ops_failure_app").unlink()
        ev.wait_for("health_status: unhealthy")
        assert docker("inspect", name, "--format", "{{.State.Health.Status}}").stdout.strip() == "unhealthy"
    finally:
        ev.p.kill()
        docker("rm", "-fv", name, check=False)


def test_base_images_are_pinned_by_digest_and_the_same_postgres_digest_is_used_everywhere():
    """재현 가능한 빌드·시험: 기반 이미지는 태그만이 아니라 digest 로 고정한다. DB 이미지·시험용 PostgreSQL 스크립트가 같은 값을 쓴다."""
    import re
    dig = re.compile(r"sha256:[0-9a-f]{64}")
    pg_docker = re.search(r"^ARG POSTGRES_IMAGE=(\S+)", (ROOT / "db" / "Dockerfile").read_text("utf-8"), re.M).group(1)
    pg_script = re.search(r"^PG_IMAGE=(\S+)", (ROOT / "scripts" / "dev-postgres.sh").read_text("utf-8"), re.M).group(1)
    assert dig.search(pg_docker) and pg_docker == pg_script
    app = (ROOT / "Dockerfile").read_text("utf-8")
    froms = re.findall(r"^FROM (\S+)", app, re.M)
    assert len(froms) == 2 and froms[0] == froms[1] and froms[0].startswith("python:3.12-slim@") and dig.search(froms[0])      # 두 단계가 같은 digest

"""백업 잡(RUNTIME R10.1, C34 admin 명령): 이 영역의 PostgreSQL 두 저장소를 논리 덤프(COPY 텍스트) 한 파일로 꺼내 restic 으로 platform 백업 저장소(restic REST)에 보낸다(스냅숏 하나).

왜 논리 덤프를 psycopg 의 COPY 로 하나: pg_dump 는 서버보다 낮은 버전이면 거부하고(이미지의 클라이언트 버전을 서버 버전에 묶어야 한다), 이 영역의 표는 넷뿐이다(ADR 0032).
스키마는 `admin migrate` 가 만들므로 데이터만 낸다. 덤프는 각 저장소(components 의 저장소 구현)가 자기 표를 읽는다: 원본 접근은 저장소 구현 안에만 있다(D15).
영역 밖으로 나가는 길은 자기 영역 게이트웨이 출구뿐이다: restic(Go)은 HTTPS_PROXY 의 CONNECT 터널을 쓴다(R1.8). 비밀은 파일에서만 읽고 어떤 출력에도 쓰지 않는다(R6.3).
결과는 공통 계약 BackupJobResult(R9.2): 종료 코드 0 ok, 75 재시도 가능(UNAVAILABLE), 1 그 밖의 실패.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Callable, Mapping
from urllib.parse import quote, urlsplit, urlunsplit

from boundary_contracts import read_secret, validator
from boundary_contracts.errors import BoundaryError

from ..components.link_egress import egress_proxy, tls_url
from .clock import Clock, utc_now
from .pgbase import DUMP_STATEMENT_SECONDS
from .storage import FAILURE_URL, LOG_URL, open_failure_store, open_log_store

# 백업 보존(데이터 최소화): 스냅숏에는 원본 로그가 들어 있으므로 원본의 보존 기간(OPS_RAW_RETENTION_DAYS, 없으면 ADR 0007 의 기본 raw_days)보다 오래 두지 않는다. ADR 0032.
DEFAULT_RAW_RETENTION_DAYS = 30           # 출처: ADR 0007(원본 기본 30일). job_advisor.DEFAULT_DAYS["raw_days"]와 같은 값

EXIT_OK, EXIT_RETRYABLE, EXIT_FAILED = 0, 75, 1      # BackupJobResult: EX_TEMPFAIL = 75


def _repository(url: str, credentials: str) -> str:
    """restic REST 저장소 주소(rest: 접두 + HTTPS 주소에 사용자·암호). 자격은 환경 변수 RESTIC_REPOSITORY 로만 하위 프로세스에 간다(argv 에 없다)."""
    tls_url(url)
    user, sep, pw = credentials.strip().partition(":")
    if not sep or not user:
        raise BoundaryError("INVALID_RECORD", "OPENIT_BACKUP_CREDENTIALS_FILE 내용은 '<사용자>:<암호>' 한 줄이어야 한다")
    p = urlsplit(url)           # 평문 http 는 tls_url 이 loopback 에서만 허용한다(R1.1): 그 밖의 http 는 위에서 ValueError
    return "rest:" + urlunsplit((p.scheme, f"{quote(user, safe='')}:{quote(pw, safe='')}@{p.netloc}", p.path, p.query, ""))


def keep_within_days(env: Mapping[str, str]) -> int:
    """restic forget --keep-within 에 줄 일 수. 원본 보존 기간 이하의 정수(소수는 내림: 더 짧게). 숫자가 아니거나 1 미만이면 거부한다('forever' 같은 무기한은 백업을 무기한 두는 것이라 받지 않는다)."""
    raw = (env.get("OPS_RAW_RETENTION_DAYS") or "").strip()
    if not raw:
        return DEFAULT_RAW_RETENTION_DAYS
    try:
        days = int(float(raw))
    except ValueError:
        days = 0
    if days < 1:
        raise BoundaryError("INVALID_RECORD", "OPS_RAW_RETENTION_DAYS 는 1 이상의 일 수여야 한다(백업 보존 기간의 상한)")
    return days


def run(env: Mapping[str, str], *, restic: str = "restic", runner: Callable = subprocess.run, clock: Clock = utc_now) -> dict:
    """백업 한 번. 성공하면 BackupJobResult(status ok), 실패는 BoundaryError. 실패 메시지에 비밀·접속 문자열이 없다."""
    started = clock()
    days = keep_within_days(env)
    url = env.get("OPENIT_BACKUP_URL")
    cred = read_secret("OPENIT_BACKUP_CREDENTIALS", environ=env)
    if not url or not cred or not env.get("OPENIT_BACKUP_REPO_PASSWORD_FILE"):
        raise BoundaryError("INVALID_RECORD", "OPENIT_BACKUP_URL, OPENIT_BACKUP_CREDENTIALS_FILE, OPENIT_BACKUP_REPO_PASSWORD_FILE 가 필요하다")
    child = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": "/tmp", "RESTIC_CACHE_DIR": "/tmp/restic-cache",
             "RESTIC_REPOSITORY": _repository(url, cred), "RESTIC_PASSWORD_FILE": env["OPENIT_BACKUP_REPO_PASSWORD_FILE"]}      # 상속 환경을 통째로 넘기지 않는다(R9.1)
    proxy = egress_proxy(env)
    if proxy:
        child["HTTPS_PROXY"] = proxy
    if env.get("SSL_CERT_FILE"):
        child["SSL_CERT_FILE"] = env["SSL_CERT_FILE"]

    def call(*argv, stdin=None):
        try:
            return runner([restic, *argv], stdin=stdin, env=child, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=DUMP_STATEMENT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as e:          # restic 이 없거나 시간 초과: 종류만 낸다
            raise BoundaryError("UNAVAILABLE", f"restic 실행 실패({type(e).__name__})") from None

    # 저장소는 서버·잡과 같은 앱 계정·verify 로 연다(스키마를 만들지 않는다, ADR 0031)
    venv = {**env, "OPS_DB_SCHEMA": "verify"}
    stores = []
    if read_secret(LOG_URL, environ=env):
        stores.append(("log", open_log_store(venv, "")))
    if read_secret(FAILURE_URL, environ=env):
        stores.append(("failure", open_failure_store(venv, None)))
    if not stores:
        raise BoundaryError("INVALID_RECORD", "백업할 PostgreSQL 저장소가 없다(OPS_LOG_DATABASE_URL_FILE, OPS_FAILURE_DATABASE_URL_FILE)")
    if call("cat", "config").returncode != 0 and call("init").returncode != 0:
        raise BoundaryError("UNAVAILABLE", "백업 저장소를 열거나 만들지 못했다")
    total = 0
    with tempfile.TemporaryFile() as f:
        for name, store in stores:
            f.write(f"-- store: {name}\n".encode())
            total += store.dump(f)
        f.seek(0)
        r = call("backup", "--stdin", "--stdin-filename", "ops-feedback.copy", "--tag", "ops-feedback", "--host", "ops-feedback", "--json", stdin=f)
    if r.returncode != 0:
        raise BoundaryError("UNAVAILABLE", "백업 저장소로 보내지 못했다")
    # 보존: 원본 보존 기간보다 오래된 스냅숏을 지운다(--prune 으로 실제 공간도 비운다). 스냅숏이 이미 만들어졌으므로 실패하면 결과는 failed(재시도 가능)다.
    if call("forget", "--keep-within", f"{days}d", "--tag", "ops-feedback", "--host", "ops-feedback", "--prune").returncode != 0:
        raise BoundaryError("UNAVAILABLE", "백업 보존 정리(forget)에 실패했다")
    snapshot_id = None
    for line in (r.stdout or b"").decode("utf-8", "replace").splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if isinstance(m, dict) and m.get("message_type") == "summary":
            snapshot_id = m.get("snapshot_id")
    out = {"status": "ok", "snapshot_id": snapshot_id, "bytes": total, "started_at": started, "finished_at": clock(), "error": None}
    _check(out)
    return out


def failed(e: BaseException, started: str, clock: Clock = utc_now) -> dict:
    """실패 결과(BackupJobResult). error 는 BoundaryError 모양(code·message·retryable). 예외 메시지는 BoundaryError 의 것만 싣는다(이미 비밀을 뺀 문구)."""
    be = e if isinstance(e, BoundaryError) else BoundaryError("INVALID_RECORD", "백업 설정 오류" if isinstance(e, (ValueError,)) else "백업 실패")
    out = {"status": "failed", "snapshot_id": None, "bytes": 0, "started_at": started, "finished_at": clock(),
           "error": {"code": be.code, "message": be.message, "retryable": be.code == "UNAVAILABLE"}}
    _check(out)
    return out


def exit_code(result: dict) -> int:
    if result["status"] == "ok":
        return EXIT_OK
    return EXIT_RETRYABLE if result["error"]["retryable"] else EXIT_FAILED


def _check(out: dict) -> None:
    errs = list(validator("BackupJobResult").iter_errors(out))      # 생산자 출력 검증(CONVENTIONS 3절)
    if errs:
        raise BoundaryError("INTEGRITY_ERROR", "BackupJobResult 를 어긴다: " + errs[0].validator)

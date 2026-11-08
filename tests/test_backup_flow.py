"""RUNTIME R10.1: 영역이 자기 데이터를 꺼내 자기 게이트웨이 출구(HTTPS_PROXY=OPENIT_EGRESS_PROXY)로 platform 백업 저장소(restic REST)에 보낸다.
가짜 restic(실행 파일 스텁)이 받은 인자·환경 이름·표준 입력을 파일에 남기고, 시험이 그것을 단언한다. 실제 PostgreSQL 에서 덤프 내용을 본다."""
import json
import os
import stat
import sys

import pytest
from boundary_contracts import validator
from boundary_contracts.errors import BoundaryError

from conftest import _pg_schema_url, envelope, make_trace
from ops_feedback.admin import __main__ as admin_cli
from ops_feedback.components.failure_case_store import FailureCaseStore
from ops_feedback.wiring import backup_flow
from ops_feedback.wiring.log_store import LogStore

SECRET_PW, SECRET_USER = "pw-backup-canary-9", "backupuser"


@pytest.fixture
def fake_restic(tmp_path):
    """호출마다 인자(JSON 한 줄)와 환경의 RESTIC_*·HTTPS_PROXY 이름·값, 표준 입력 크기를 calls.jsonl 에 쓴다. cat config 는 실패해 init 을 부르게 한다."""
    log = tmp_path / "calls.jsonl"
    exe = tmp_path / "restic"
    exe.write_text(f"""#!{sys.executable}
import json, os, sys
inp = sys.stdin.buffer.read() if "backup" in sys.argv else b""
open({str(log)!r}, "a").write(json.dumps({{"argv": sys.argv[1:], "env": {{k: v for k, v in os.environ.items() if k.startswith(("RESTIC", "HTTPS", "SSL"))}}, "stdin": inp.decode("utf-8", "replace")}}) + "\\n")
if "backup" in sys.argv: print(json.dumps({{"message_type": "summary", "snapshot_id": "snap1234"}}))
sys.exit(1 if sys.argv[1:3] == ["cat", "config"] else 0)
""")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe, log


def _env(tmp_path, log_url, fail_url):
    (tmp_path / "cred").write_text(f"{SECRET_USER}:{SECRET_PW}\n")
    (tmp_path / "rp").write_text("repo-password\n")
    (tmp_path / "lu").write_text(log_url)
    (tmp_path / "fu").write_text(fail_url)
    return {"OPENIT_BACKUP_URL": "https://backup.example/ops-feedback", "OPENIT_BACKUP_CREDENTIALS_FILE": str(tmp_path / "cred"),
            "OPENIT_BACKUP_REPO_PASSWORD_FILE": str(tmp_path / "rp"), "OPENIT_EGRESS_PROXY": "http://127.0.0.1:3128",
            "OPS_LOG_DATABASE_URL_FILE": str(tmp_path / "lu"), "OPS_FAILURE_DATABASE_URL_FILE": str(tmp_path / "fu")}


def test_backup_sends_dump_through_the_egress_proxy_without_leaking_secrets(request, tmp_path, fake_restic):
    exe, log = fake_restic
    log_url, fail_url = _pg_schema_url(request), _pg_schema_url(request)
    store = LogStore.postgres(log_url)
    FailureCaseStore.postgres(fail_url)._b.close()
    store.send(envelope(make_trace("s-bk", "t-bk")))
    out = backup_flow.run(_env(tmp_path, log_url, fail_url), restic=str(exe), clock=lambda: "2026-11-04T00:00:00Z")
    assert out["status"] == "ok" and out["snapshot_id"] == "snap1234" and out["bytes"] > 0 and out["error"] is None and not list(validator("BackupJobResult").iter_errors(out))
    calls = [json.loads(l) for l in log.read_text().splitlines()]
    assert [c["argv"][0] for c in calls] == ["cat", "init", "backup", "forget"]               # 저장소가 없으면 만든다, 스냅숏 하나, 보존 정리
    for c in calls:
        assert c["env"]["HTTPS_PROXY"] == "http://127.0.0.1:3128"                              # 영역 게이트웨이 출구로만 나간다
        assert c["env"]["RESTIC_REPOSITORY"].startswith("rest:https://") and SECRET_PW not in " ".join(c["argv"])      # 자격은 argv 에 없다
    b = calls[2]
    assert b["argv"][:3] == ["backup", "--stdin", "--stdin-filename"] and b["argv"][3] == "ops-feedback.copy"
    fg = calls[3]["argv"]
    assert fg[:3] == ["forget", "--keep-within", "30d"] and "--prune" in fg           # 원본 기본 보존 30일(ADR 0007) 이하: 백업을 원본보다 오래 두지 않는다
    d = b["stdin"]
    assert "-- store: log" in d and "COPY raw_traces FROM STDIN;" in d and "s-bk" in d and "-- store: failure" in d and "COPY cases FROM STDIN;" in d      # 덤프 안에 실제 행이 있다
    assert d.rstrip().endswith("\\.")


def test_cli_prints_counts_only_and_hides_secrets(request, tmp_path, fake_restic, monkeypatch, capsys):
    exe, _ = fake_restic
    log_url = _pg_schema_url(request)
    LogStore.postgres(log_url)._raw._backend.close()
    for k in [k for k in os.environ if k.startswith(("OPS_", "OPENIT_"))]:
        monkeypatch.delenv(k)
    for k, v in _env(tmp_path, log_url, log_url).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PATH", f"{exe.parent}:{os.environ['PATH']}")
    FailureCaseStore.postgres(log_url)._b.close()
    assert admin_cli.main(["backup"]) == 0
    text = capsys.readouterr().out
    res = json.loads(text.splitlines()[-1])
    assert res["status"] == "ok" and res["snapshot_id"] == "snap1234" and SECRET_PW not in text and log_url not in text


@pytest.mark.parametrize("drop", ["OPENIT_BACKUP_URL", "OPENIT_BACKUP_CREDENTIALS_FILE", "OPENIT_BACKUP_REPO_PASSWORD_FILE"])
def test_missing_backup_setting_is_refused_before_any_call(tmp_path, fake_restic, drop):
    exe, log = fake_restic
    env = _env(tmp_path, "postgresql://u@h/d", "postgresql://u@h/d")
    env.pop(drop)
    with pytest.raises(BoundaryError) as e:
        backup_flow.run(env, restic=str(exe))
    assert e.value.code == "INVALID_RECORD" and not log.exists()


def test_plain_http_backup_url_is_refused(tmp_path, fake_restic):
    exe, log = fake_restic
    env = _env(tmp_path, "postgresql://u@h/d", "postgresql://u@h/d") | {"OPENIT_BACKUP_URL": "http://backup.example/x"}
    with pytest.raises(ValueError):
        backup_flow.run(env, restic=str(exe))
    assert not log.exists()


def test_restic_failure_is_exit_75_with_a_failed_result_and_no_secrets(request, tmp_path, monkeypatch, capsys):
    """R9.2·BackupJobResult: 백업 저장소에 닿지 못하면 종료 코드 75(재시도 가능), 마지막 줄은 status failed 인 BackupJobResult(비밀 없음)."""
    bad = tmp_path / "restic"
    bad.write_text("#!/bin/sh\nexit 3\n")
    bad.chmod(0o755)
    log_url = _pg_schema_url(request)
    LogStore.postgres(log_url)._raw._backend.close()
    for k in [k for k in os.environ if k.startswith(("OPS_", "OPENIT_"))]:
        monkeypatch.delenv(k)
    for k, v in _env(tmp_path, log_url, log_url).items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("OPS_FAILURE_DATABASE_URL_FILE")
    monkeypatch.setenv("PATH", f"{bad.parent}:{os.environ['PATH']}")
    assert admin_cli.main(["backup"]) == 75
    text = capsys.readouterr().out
    res = json.loads(text.splitlines()[-1])
    assert res["status"] == "failed" and res["snapshot_id"] is None and res["error"]["code"] == "UNAVAILABLE" and res["error"]["retryable"] is True
    assert not list(validator("BackupJobResult").iter_errors(res)) and SECRET_PW not in text and log_url not in text


def test_bad_backup_setting_is_exit_1(tmp_path, monkeypatch, capsys):
    for k in [k for k in os.environ if k.startswith(("OPS_", "OPENIT_"))]:
        monkeypatch.delenv(k)
    assert admin_cli.main(["backup"]) == 1
    res = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert res["status"] == "failed" and res["error"]["code"] == "INVALID_RECORD" and res["error"]["retryable"] is False


def test_backup_retention_follows_the_raw_retention_and_refuses_unbounded(request, tmp_path, fake_restic):
    """백업 보존은 원본 보존 기간(OPS_RAW_RETENTION_DAYS) 이하다. 14일이면 14d, 소수는 내림, 무기한·0·숫자 아님은 거부(백업이 원본보다 오래 남지 않게)."""
    exe, log = fake_restic
    log_url = _pg_schema_url(request)
    LogStore.postgres(log_url)._raw._backend.close()
    env = _env(tmp_path, log_url, log_url)
    del env["OPS_FAILURE_DATABASE_URL_FILE"]
    backup_flow.run(env | {"OPS_RAW_RETENTION_DAYS": "14.9"}, restic=str(exe))
    forget = [json.loads(l)["argv"] for l in log.read_text().splitlines() if json.loads(l)["argv"][0] == "forget"]
    assert forget[0][2] == "14d"
    assert backup_flow.keep_within_days({}) == 30             # 기본은 ADR 0007 의 원본 기본 보존 30일
    for bad in ("forever", "0", "-3", "abc", "inf", "1e999", "nan"):
        with pytest.raises(BoundaryError) as e:
            backup_flow.keep_within_days({"OPS_RAW_RETENTION_DAYS": bad})
        assert e.value.code == "INVALID_RECORD"


def test_restore_job_exit_codes_and_refusals(request, tmp_path, monkeypatch, capsys):
    """복구 잡의 종료 코드(BackupJobResult 규칙): restic 불통 -> 75, 스냅숏 없음 -> 1(FAILED_PRECONDITION), 대상 DB 설정 없음 -> 1. 비밀이 출력에 없다."""
    log_url = _pg_schema_url(request)
    LogStore.postgres(log_url)._raw._backend.close()
    for k in [k for k in os.environ if k.startswith(("OPS_", "OPENIT_"))]:
        monkeypatch.delenv(k)
    for k, v in _env(tmp_path, log_url, log_url).items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("OPS_FAILURE_DATABASE_URL_FILE")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "restic").write_text("#!/bin/sh\nexit 3\n")
    (bad / "restic").chmod(0o755)
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "restic").write_text("#!/bin/sh\n[ \"$1\" = snapshots ] && echo '[]' && exit 0\nexit 3\n")
    (empty / "restic").chmod(0o755)
    def run_cli(path_dir):
        monkeypatch.setenv("PATH", f"{path_dir}:{os.environ['PATH']}")
        code = admin_cli.main(["restore"])
        text = capsys.readouterr().out
        assert SECRET_PW not in text and log_url not in text and not list(validator("BackupJobResult").iter_errors(json.loads(text.splitlines()[-1])))
        return code, json.loads(text.splitlines()[-1])
    code, res = run_cli(bad)
    assert code == 75 and res["error"]["code"] == "UNAVAILABLE"
    code, res = run_cli(empty)
    assert code == 1 and res["error"]["code"] == "FAILED_PRECONDITION" and res["status"] == "failed"
    monkeypatch.delenv("OPS_LOG_DATABASE_URL_FILE")
    code, res = run_cli(empty)
    assert code == 1 and res["error"]["code"] == "INVALID_RECORD"


def test_load_refuses_a_non_empty_target_and_a_mismatching_dump(request):
    """복구 적재(PgBase.load): 대상 표가 비어 있지 않으면 FAILED_PRECONDITION(덮어쓰지 않는다), 구획의 표가 저장소의 표와 다르면 INVALID_RECORD."""
    store = LogStore.postgres(_pg_schema_url(request))
    store.load("COPY raw_traces FROM STDIN;\n\\.\n\n")            # 빈 표에는 빈 구획이 들어간다(오류 없음)
    store.send(envelope(make_trace("s-ld", "t-1")))
    with pytest.raises(BoundaryError) as e:
        store.load("COPY raw_traces FROM STDIN;\n\\.\n\n")
    assert e.value.code == "FAILED_PRECONDITION"
    other = LogStore.postgres(_pg_schema_url(request))
    with pytest.raises(BoundaryError) as e:
        other.load("COPY cases FROM STDIN;\n\\.\n\n")
    assert e.value.code == "INVALID_RECORD"

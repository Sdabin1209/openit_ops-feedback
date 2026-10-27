"""비밀(토큰, salt)은 NAME_FILE로 받고, 값은 어떤 출력에도 나오지 않는다(D84)."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest
import yaml

from conftest import envelope, make_trace, pii_trace, base_config
from ops_feedback.job import build_from_config
from ops_feedback.logstore import LogStore

SALT = "SALT-canary-7f3a91"
ING, RD, RV = "ing-canary-11aa", "rd-canary-22bb", "rv-canary-33cc"


def _scrub_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPS_")}
    env.update(extra)
    return env


def _cfg(tmp_path):
    cfg = base_config()
    cfg["storage"] = {"log_db": str(tmp_path / "log.db"), "failure_db": str(tmp_path / "f.db")}
    p = tmp_path / "job.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return cfg, p


def _secret_file(tmp_path, name, value):
    f = tmp_path / name
    f.write_text(value + "\n")
    return str(f)


def test_job_reads_salt_from_file_and_never_prints_it(tmp_path):
    cfg, p = _cfg(tmp_path)
    log = LogStore.sqlite(cfg["storage"]["log_db"])
    log.send(envelope(pii_trace("s-1", "t-1")))
    log.send(envelope(make_trace("s-1", "t-2")))
    env = _scrub_env(OPS_DEID_SALT_FILE=_secret_file(tmp_path, "salt", SALT))
    r = subprocess.run([sys.executable, "-m", "ops_feedback.job", "--config", str(p)], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert SALT not in r.stdout and SALT not in r.stderr
    assert json.loads(r.stdout)["cases_published"] == 1
    # 비식별 산출물과 DB 파일에도 salt 원문이 없다
    for f in tmp_path.glob("*.db"):
        assert SALT.encode() not in f.read_bytes()


def test_file_wins_over_env_and_env_still_works(tmp_path):
    cfg, _ = _cfg(tmp_path)
    f = _secret_file(tmp_path, "salt", "from-file")
    a = build_from_config(cfg, {"OPS_DEID_SALT_FILE": f, "OPS_DEID_SALT": "from-env"})[2]
    b = build_from_config(cfg, {"OPS_DEID_SALT": "from-file"})[2]
    tr = envelope(pii_trace("s-1", "t-1"))
    assert a.deidentify(tr) == b.deidentify(tr)         # 파일 값이 우선


def test_missing_or_bad_salt_error_hides_values(tmp_path):
    cfg, _ = _cfg(tmp_path)
    with pytest.raises(ValueError) as e:
        build_from_config(cfg, {})
    assert "OPS_DEID_SALT" in str(e.value)
    with pytest.raises(Exception) as e:
        build_from_config(cfg, {"OPS_DEID_SALT_FILE": str(tmp_path / "none"), "OPS_DEID_SALT": SALT})
    assert SALT not in str(e.value) and SALT not in repr(e.value)
    empty = _secret_file(tmp_path, "empty", "")
    with pytest.raises(Exception) as e:
        build_from_config(cfg, {"OPS_DEID_SALT_FILE": empty, "OPS_DEID_SALT": SALT})
    assert SALT not in str(e.value)


def test_factory_arg_token_from_file(tmp_path):
    from ops_feedback.job import _resolve_args
    f = _secret_file(tmp_path, "gt", "gate-canary")
    assert _resolve_args({"token": {"env": "OPS_GATE_READ_TOKEN"}, "u": "x"}, {"OPS_GATE_READ_TOKEN_FILE": f}) == {"token": "gate-canary", "u": "x"}
    with pytest.raises(ValueError) as e:
        _resolve_args({"token": {"env": "OPS_GATE_READ_TOKEN"}}, {})
    assert "gate-canary" not in str(e.value)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _get(url, token=None):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_api_server_secrets_from_files_do_not_leak(tmp_path):
    port = _free_port()
    env = _scrub_env(
        OPS_INGEST_TOKEN_FILE=_secret_file(tmp_path, "i", ING),
        OPS_READ_TOKENS_FILE=_secret_file(tmp_path, "r", f"synthetic-data={RD}"),
        OPS_REVIEW_TOKENS_FILE=_secret_file(tmp_path, "v", f"reviewer={RV}"),
        OPS_LOG_DB=str(tmp_path / "l.db"), OPS_FAILURE_DB=str(tmp_path / "f.db"), OPS_PORT=str(port))
    pr = subprocess.Popen([sys.executable, "-m", "ops_feedback.api"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    bodies = []
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                bodies.append(_get(base + "/healthz")[1])
                break
            except OSError:
                time.sleep(0.2)
        else:
            pytest.fail("서버가 뜨지 않았다")
        bodies.append(_get(base + "/readyz")[1])
        for tok in (None, "wrong", RD, RV):                       # 잘못된 토큰의 오류 본문
            bodies.append(_get(base + "/v1/cases", tok)[1])
        assert _get(base + "/v1/cases", RD)[0] == 200             # 파일에서 읽은 토큰이 실제로 동작
        bodies.append(_get(base + "/v1/cases/nope", RD)[1])
        bodies.append(_get(base + "/v1/cases", ING)[1])
    finally:
        pr.terminate()
        out = pr.communicate(timeout=10)[0]
    for secret in (ING, RD, RV):
        assert secret not in out
        assert all(secret not in b for b in bodies)


def test_api_startup_errors_hide_values(tmp_path):
    env = _scrub_env(OPS_INGEST_TOKEN=ING, OPS_INGEST_TOKEN_FILE=str(tmp_path / "missing"), OPS_PORT=str(_free_port()))
    r = subprocess.run([sys.executable, "-m", "ops_feedback.api"], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and ING not in r.stdout + r.stderr
    r = subprocess.run([sys.executable, "-m", "ops_feedback.api"], env=_scrub_env(OPS_PORT=str(_free_port())), capture_output=True, text=True, timeout=30)
    assert r.returncode == 2


# ---- 설정에 비밀 값을 직접 적으면 시작을 거부한다 ----
INLINE = "inline-canary-9d8e"


@pytest.mark.parametrize("patch,key", [
    (lambda c: c.__setitem__("release_reader", {"factory": "x:y", "args": {"token": INLINE}}), "release_reader.args.token"),
    (lambda c: c.__setitem__("gate_service", {"factory": "x:y", "args": {"headers": [{"Authorization": INLINE}]}}), "gate_service.args.headers[0].Authorization"),
    (lambda c: c["deid"].__setitem__("api_key", INLINE), "deid.api_key"),
    (lambda c: c.__setitem__("x", {"nested": {"db_password": INLINE}}), "x.nested.db_password"),
])
def test_inline_secret_in_config_is_rejected_without_value(tmp_path, patch, key):
    from ops_feedback.config import load_config
    cfg = base_config()
    patch(cfg)
    p = tmp_path / "job.yaml"
    p.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError) as e:
        load_config(p)
    assert key in str(e.value) and "NAME_FILE" in str(e.value)
    assert INLINE not in str(e.value) and INLINE not in repr(e.value)


def test_config_accepts_secret_references_and_env_name_keys(tmp_path):
    from ops_feedback.config import load_config
    cfg = base_config()
    cfg["release_reader"] = {"factory": "x:y", "args": {"token": {"secret": "A"}, "t2": {"env": "B"}, "token_env": "C"}}
    p = tmp_path / "job.yaml"
    p.write_text(yaml.safe_dump(cfg))
    load_config(p)                              # 거부하지 않는다 (deid.salt_env도 이름만)


def test_resolve_args_secret_form_and_nesting(tmp_path):
    from ops_feedback.job import _resolve_args
    f = _secret_file(tmp_path, "s", "v-canary")
    out = _resolve_args({"a": {"secret": "S"}, "n": {"h": [{"secret": "S"}]}, "k": 1}, {"S_FILE": f})
    assert out == {"a": "v-canary", "n": {"h": ["v-canary"]}, "k": 1}


def test_cli_rejects_inline_secret_without_printing_it(tmp_path):
    cfg, p = _cfg(tmp_path)
    cfg["release_reader"] = {"factory": "x:y", "args": {"token": INLINE}}
    p.write_text(yaml.safe_dump(cfg))
    r = subprocess.run([sys.executable, "-m", "ops_feedback.job", "--config", str(p)], env=_scrub_env(OPS_DEID_SALT="s"), capture_output=True, text=True)
    assert r.returncode != 0 and INLINE not in r.stdout + r.stderr and "release_reader.args.token" in r.stderr

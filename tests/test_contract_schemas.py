"""계약에 올라간 이 영역의 경계 스키마(OpsFeedback*, D107)를 코드가 실제로 쓰는지 확인한다.
기대값은 계약 스키마와 conformance 예시에서 온다. 예시 폴더는 계약 리포지토리에만 있어(패키지에 없다) OPENIT_CONFORMANCE_DIR 또는
이웃 `../boundary-contracts/conformance`에서 찾고, 없으면 그 시험만 건너뛴다(아래 인라인 사례는 항상 돈다)."""
from waiting import HANG_GUARD_SECONDS
import copy
import json
import os
import re
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import pytest
import yaml
from boundary_contracts import validator

import deps
from conftest import base_config, envelope, make_trace, pii_trace
from ops_feedback.config import load_config, validate_server_env
from ops_feedback.components.deidentifier import DeidError, DeidPolicy
from tdeid import Deidentifier

ROOT = Path(__file__).resolve().parents[1]


def _conformance() -> Path:
    return deps.conformance()


def _examples(kind: str, prefix: str):
    d = _conformance()
    return sorted((d / kind).glob(prefix + ".*json"))


def _write_cfg(tmp_path, cfg) -> Path:
    p = tmp_path / "job.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


# ---- OpsFeedbackJobConfig ----

def test_job_config_rejects_unknown_keys_inline(tmp_path):
    typo = base_config() | {"rollbak": {}}
    with pytest.raises(ValueError):
        load_config(_write_cfg(tmp_path, typo))
    typo2 = base_config()
    typo2["retention"]["raw_dayz"] = 3
    with pytest.raises(ValueError):
        load_config(_write_cfg(tmp_path, typo2))


def test_job_config_example_file_is_valid():
    cfg = load_config(ROOT / "config" / "job.example.yaml")
    assert validator("OpsFeedbackJobConfig").is_valid(cfg)


@pytest.mark.parametrize("p", _examples("examples_invalid", "OpsFeedbackJobConfig"), ids=lambda p: p.name)
def test_job_config_contract_invalid_examples_rejected(p, tmp_path):
    with pytest.raises(ValueError):
        load_config(_write_cfg(tmp_path, json.loads(p.read_text())))


@pytest.mark.parametrize("p", _examples("examples", "OpsFeedbackJobConfig"), ids=lambda p: p.name)
def test_job_config_contract_valid_examples_accepted(p, tmp_path):
    load_config(_write_cfg(tmp_path, json.loads(p.read_text())))


# ---- OpsFeedbackServerEnv ----

def test_server_env_rejects_unknown_ops_variable():
    with pytest.raises(ValueError, match="OPS_RAW_RETENTON_DAYS"):
        validate_server_env({"OPS_PORT": "8080", "OPS_RAW_RETENTON_DAYS": "30"})
    with pytest.raises(ValueError, match="OPS_DB_SCHEMA"):
        validate_server_env({"OPS_DB_SCHEMA": "skip"})
    validate_server_env({"PATH": "/usr/bin", "OPS_PORT": "8080", "OPS_DB_SCHEMA": "verify"})     # OPS_ 아닌 변수는 대상이 아니다


@pytest.mark.parametrize("p", _examples("examples_invalid", "OpsFeedbackServerEnv"), ids=lambda p: p.name)
def test_server_env_contract_invalid_examples_rejected(p):
    ex = json.loads(p.read_text())
    if p.name.endswith("unknown-build-arg.json"):
        pytest.skip("build_args는 아래 Dockerfile 시험이 본다")
    with pytest.raises(ValueError):
        validate_server_env(ex["env"])


def test_server_env_contract_valid_example_accepted():
    for p in _examples("examples", "OpsFeedbackServerEnv"):
        validate_server_env(json.loads(p.read_text())["env"])


def test_api_main_refuses_unknown_ops_variable(tmp_path):
    env = {**os.environ, "OPS_INGEST_TOKEN": "t", "OPS_LOG_DB": str(tmp_path / "l.db"), "OPS_RAW_RETENTON_DAYS": "30"}
    r = subprocess.run([sys.executable, "-m", "ops_feedback.api"], env=env, capture_output=True, text=True, timeout=HANG_GUARD_SECONDS)      # 매달림 감시
    assert r.returncode == 2 and "OPS_RAW_RETENTON_DAYS" in (r.stdout + r.stderr)


def test_dockerfile_build_args_and_labels_follow_contract():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    args = dict(re.findall(r"^ARG (\w+)=(\S+)", text, re.M))
    assert validator("OpsFeedbackServerEnv").is_valid({"env": {}, "build_args": args}), args       # 모르는 ARG 없음
    for k, v in args.items():
        assert v not in ("none", "unknown", "unset"), f"ARG {k} 기본값 {v}: 값 없음 표기는 n/a 하나다"
    labels = dict(re.findall(r'(io\.openit\.[\w.-]+)="?\$(\w+)"?', text))
    assert set(labels) == {"io.openit.service", "io.openit.revision", "io.openit.contracts.boundary", "io.openit.contracts.survey-schema",
                           "io.openit.model-runtime"} or "io.openit.service" in text
    # 실제 값으로 채운 라벨이 ImageLabels를 지키는지: 기본값이 있는 선택 라벨은 기본값 그대로, 나머지는 빌드가 넘기는 형식의 값으로
    given = {"COMMIT": "6e830e8", "CONTRACTS_VERSION": "v2.2.1", "SURVEY_SCHEMA_VERSION": "1"}
    filled = {"io.openit.service": "ops-feedback"}
    for key, var in labels.items():
        filled[key] = given.get(var, args.get(var))
    assert validator("ImageLabels").is_valid(filled), filled


# ---- OpsFeedbackDeidentifiedTrace: 비식별기 출력 자기 검사 ----

def _deid():
    cfg = base_config()
    return Deidentifier(DeidPolicy.from_config(cfg["deid"]), "test-salt-0123456789abcdef-0123456789abcdef")


def test_deid_output_is_valid_deidentified_trace():
    res = _deid().deidentify(envelope(pii_trace()))
    errs = list(validator("OpsFeedbackDeidentifiedTrace").iter_errors(res.trace))
    assert not errs, [e.message[:120] for e in errs[:3]]


def test_deid_output_violating_contract_fails_closed(monkeypatch):
    import ops_feedback.components.deidentifier as deid_mod
    real = deid_mod._Walker.walk

    def bad(self, node, schema, path, key):
        out = real(self, node, schema, path, key)
        if path == "" and isinstance(out, dict):
            out["raw_unknown_key"] = "원본 조각"          # 스키마가 모르는 키 = 허용 목록 밖
        return out
    monkeypatch.setattr(deid_mod._Walker, "walk", bad)
    with pytest.raises(DeidError):
        _deid().deidentify(envelope(pii_trace()))


# ---- OpsFeedbackReadyz / OpsFeedbackJobResult: 출력 자기 검사 ----

def test_readyz_bodies_follow_contract():
    from ops_feedback.wiring.ingest_service import make_ingest_server as make_server
    from ops_feedback.wiring.log_store import LogStore
    srv = make_server(LogStore.memory(), "tok", "127.0.0.1", 0, retention_declared=False)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        body = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/readyz").read())
    finally:
        srv.shutdown()
    assert body["warnings"] == ["RAW_RETENTION_UNSET"]
    assert not list(validator("OpsFeedbackReadyz").iter_errors(body))


def test_job_result_follows_contract(pipeline):
    from ops_feedback.wiring.pipeline import run_once
    pipeline["log"].send(envelope(pii_trace("s-1", "t-1")))
    rep = run_once(cfg=pipeline["cfg"], log=pipeline["log"], store=pipeline["store"], deid=pipeline["deid"], detector=pipeline["detector"], pseudonymize=pipeline["pseudonymize"],
                   classifier=pipeline["classifier"], clock=pipeline["clock"])
    assert not list(validator("OpsFeedbackJobResult").iter_errors(rep.as_dict()))


def test_job_result_violation_is_not_printed(tmp_path, monkeypatch, capsys):
    import ops_feedback.job.__main__ as job
    from ops_feedback.wiring.pipeline import JobReport
    cfg = base_config()
    cfg["storage"] = {"log_db": str(tmp_path / "l.db"), "failure_db": str(tmp_path / "f.db")}
    monkeypatch.setenv("OPS_DEID_SALT", "test-salt-0123456789abcdef-0123456789abcdef")
    monkeypatch.setattr(JobReport, "as_dict", lambda self: {"processed": 0, "turn_id": "raw-t-1"})
    assert job.main(["--config", str(_write_cfg(tmp_path, cfg))]) != 0
    assert "raw-t-1" not in capsys.readouterr().out

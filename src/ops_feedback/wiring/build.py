"""설정에서 조각들을 만들어 잇는 조립(build_from_config). 비밀은 파일로 받는다(D84): 이름은 설정이, 값은 read_secret이 읽는다."""
from __future__ import annotations

import importlib

from boundary_contracts import read_secret

from ..components.deidentifier import Deidentifier, DeidPolicy
from ..components.failure_classifier import FailureClassifier
from ..components.failure_detector import FailureDetector
from ..components.pseudonymizer import check_salt, pseudonymize as _pseudonymize
from .clock import Clock, utc_now
from .rollback_flow import RollbackPlanner
from .storage import open_failure_store, open_log_store


def _resolve_args(args, env: dict, path: str = "args"):
    """설정의 {secret: 이름} 또는 {env: 이름}을 read_secret(이름)으로 푼다(중첩 포함). 비밀 값을 설정 파일에 두지 않는다."""
    if isinstance(args, dict):
        if set(args) in ({"secret"}, {"env"}):
            name = args.get("secret", args.get("env"))
            if not isinstance(name, str) or not name:
                raise ValueError(f"설정 {path}: 비밀 이름은 비어 있지 않은 문자열이어야 한다")
            val = read_secret(name, environ=env)      # NAME_FILE 우선, 없으면 NAME
            if not val:      # 없거나 빈 파일/값: 빈 비밀은 없는 것과 같다
                raise ValueError(f"비밀 {name}가 없다(NAME_FILE 또는 NAME)")
            return val
        return {k: _resolve_args(v, env, f"{path}.{k}") for k, v in args.items()}
    if isinstance(args, list):
        return [_resolve_args(v, env, f"{path}[{i}]") for i, v in enumerate(args)]
    return args


def _import(path: str):
    mod, _, fn = path.partition(":")
    return getattr(importlib.import_module(mod), fn)


def make_pseudonymizer(salt: str):
    """(key, value) -> 의사 식별자. salt는 이 닫힌 함수 안에만 있다."""
    return lambda key, value: _pseudonymize({"salt": salt, "key": key, "value": value})


def build_from_config(cfg: dict, env: dict, *, clock: Clock = utc_now):
    """-> (log, store, deid, pseudonymize, detector, classifier, planner, reader)"""
    policy = DeidPolicy.from_config(cfg["deid"])
    salt_name = cfg["deid"].get("salt_env", "OPS_DEID_SALT")
    salt = read_secret(salt_name, environ=env)          # NAME_FILE 우선, 없으면 NAME
    if not salt:
        raise ValueError(f"의사 식별자 salt 비밀({salt_name}_FILE 또는 {salt_name})이 없다")
    check_salt(salt)          # 짧은 salt는 저장소를 열기 전에 거부한다(ADR 0017)
    log = open_log_store(env, cfg["storage"]["log_db"], clock=clock)
    store = open_failure_store(env, cfg["storage"]["failure_db"])
    pseudonymize = make_pseudonymizer(salt)
    deid = Deidentifier(policy, pseudonymize)
    detector = FailureDetector(cfg["detection"]["detectors"])
    classifier = FailureClassifier(cfg["classification"])
    planner = None
    rb = cfg.get("rollback")
    rr = cfg.get("release_reader")
    reader = _import(rr["factory"])(**_resolve_args(rr.get("args") or {}, env, "release_reader.args")) if rr else None
    if rb and rr:
        gate = _import(cfg["gate_service"]["factory"])(**_resolve_args(cfg["gate_service"].get("args") or {}, env, "gate_service.args")) if cfg.get("gate_service") else None
        planner = RollbackPlanner(store=store, log=log, release_reader=reader, gate=gate, policy=rb, clock=clock)
    return log, store, deid, pseudonymize, detector, classifier, planner, reader

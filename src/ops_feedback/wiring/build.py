"""설정에서 조각들을 만들어 잇는 조립(build_from_config). 비밀은 파일로 받는다(D84): 이름은 설정이, 값은 read_secret이 읽는다."""
from __future__ import annotations


from boundary_contracts import read_secret

from ..components.link_egress import egress_proxy
from ..components.deidentifier import Deidentifier, DeidPolicy
from ..components.failure_classifier import FailureClassifier
from ..components.failure_detector import FailureDetector
from ..components.pseudonymizer import check_salt, pseudonymize as _pseudonymize
from .clock import Clock, utc_now
from .rollback_flow import RollbackPlanner
from .storage import open_failure_store, open_log_store


def _http_client(name: str, settings: dict, env: dict, egress):
    """계약 HttpClientSettings(4.0.0, D135)로 역할에 정해진 계약 클라이언트를 만든다. 구현 모듈 경로(factory)는 받지 않는다.
    토큰은 token_secret 이름으로 read_secret(NAME_FILE 우선)이 읽고, 출구는 환경의 OPENIT_EGRESS_PROXY 만 쓴다(R1.8). timeout 이 없으면 클라이언트 기본(R10.3)."""
    from boundary_contracts import clients
    tok = read_secret(settings["token_secret"], environ=env)
    if not tok:
        raise ValueError(f"비밀 {settings['token_secret']}가 없다(NAME_FILE 또는 NAME)")
    kw = {"timeout": settings["timeout_seconds"]} if "timeout_seconds" in settings else {}
    return getattr(clients, name)(settings["base_url"], tok, egress_proxy=egress, **kw)


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
    # 영역 밖(release-gate) 호출은 자기 영역 게이트웨이 출구로만 나간다: OPENIT_EGRESS_PROXY 하나만 꺼내 넘긴다(RUNTIME R1.8, CONVENTIONS 13절). 없으면 None(출구 없는 개발·시험 구성).
    egress = egress_proxy(env)
    reader = _http_client("HttpReleaseReader", rr, env, egress) if rr else None
    if rb and rr:
        gate = _http_client("HttpGateService", cfg["gate_service"], env, egress) if cfg.get("gate_service") else None
        planner = RollbackPlanner(store=store, log=log, release_reader=reader, gate=gate, policy=rb, clock=clock)
    return log, store, deid, pseudonymize, detector, classifier, planner, reader

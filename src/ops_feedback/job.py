"""주기 잡: 비식별 -> 실패 탐지 -> 분류 -> 사례 게시(-> 롤백 평가). 한 번 실행하고 끝난다(D39).

멱등: 같은 Trace를 두 번 처리해도 같은 사례가 나온다. case_id, trace ref, created_at이 모두 입력에서 결정된다.
명령: ops-feedback-job --config config/job.example.yaml [--limit N] [--reprocess] [--retry-deid-failed] [--no-rollback]
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from boundary_contracts.canonical import sha256_hash
from boundary_contracts.errors import BoundaryError

from .clock import Clock, fmt_ts, parse_ts, utc_now
from .config import load_config
from .deid import DeidError, Deidentifier, DeidPolicy
from .detect import Classifier, Detector, build_detectors
from .failurestore import FailureStoreImpl
from .logstore import LogStore
from .rollback import RollbackPlanner

from boundary_contracts import PIPELINE_SCHEMA_VERSION  # 새 레코드는 이 버전으로 쓴다(1.1). 읽기는 1.0·1.1 모두


@dataclass
class JobReport:
    processed: int = 0
    cases_published: int = 0
    cases_existing: int = 0
    no_failure: int = 0
    deid_failed: int = 0
    skipped_diff: int = 0
    purged_raw: int = 0
    rollback: Optional[dict] = None
    errors: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _question_set(reader, release_id: str, cache: dict) -> Optional[dict]:
    """source_release_id의 ReleaseManifest(I4)에서 문항 세트 ref. 읽을 수 없으면 None(필드 생략, 1.0 호환)."""
    if reader is None:
        return None
    if release_id not in cache:
        try:
            qs = reader.get_release(release_id).get("question_set")
            cache[release_id] = dict(qs) if isinstance(qs, dict) else None
        except BoundaryError:
            cache[release_id] = None
    return cache[release_id]


def case_id_for(deid: Deidentifier, session_id: str, turn_id: str) -> str:
    return "fc-" + sha256_hash({"s": deid.pseudonym("case", session_id), "t": deid.pseudonym("case", turn_id)})[7:39]


def run_once(*, cfg: dict, log: LogStore, store: FailureStoreImpl, deid: Deidentifier, detectors: list[Detector],
             classifier: Classifier, planner: Optional[RollbackPlanner] = None, clock: Clock = utc_now,
             limit: Optional[int] = None, reprocess: bool = False, retry_deid_failed: bool = False,
             evaluate_rollback: bool = True, release_reader=None) -> JobReport:
    rep = JobReport()
    qs_cache: dict = {}
    cfg_hash = sha256_hash({"deid": deid.policy.fingerprint(), "detection": cfg["detection"],
                            "classification": cfg["classification"]})
    if reprocess:
        log.reset_processing(None)
    elif retry_deid_failed:
        log.reset_processing("DEID_FAILED")

    for row in deid.pending(limit):                       # 원본을 읽는 유일한 경로
        env = row["envelope"]
        sid, tid = env["session_id"], env["turn_id"]
        rep.processed += 1
        try:
            res = deid.deidentify(env)
        except DeidError as e:
            log.mark(sid, tid, "DEID_FAILED", cfg_hash, clock())
            rep.deid_failed += 1
            rep.errors.append({"turn": deid.pseudonym("case", tid), "error": str(e)})
            continue
        detections = [d for det in detectors for d in det.detect(res.trace)]
        if not detections:
            log.mark(sid, tid, "NO_FAILURE", cfg_hash, clock())
            rep.no_failure += 1
            continue
        cls = classifier.classify(detections)
        trace_ref = "tr-" + sha256_hash(res.trace)[7:39]
        store.put_trace(trace_ref, res.trace)
        case = {
            "pipeline_schema_version": PIPELINE_SCHEMA_VERSION,
            "case_id": case_id_for(deid, sid, tid),
            "version": 1,
            "created_at": row["received_at"],
            "source_release_id": env["release_id"],
            "runtime_contract_version": env["runtime_contract_version"],
            "deidentified": True,
            "deidentified_trace_ref": trace_ref,
            "failure_type": cls.failure_type,
            "detection_method": sorted({d.method for d in detections})[0],
            "review_status": "PENDING",
            "ext": {"ops-feedback": {
                "detections": [{"rule_id": d.rule_id, "detail": d.detail} for d in sorted(detections, key=lambda d: d.rule_id)],
                "primary_rule": cls.primary_rule,
                "deid_policy_id": deid.policy.policy_id,
                "deid_stats": res.stats,
            }},
        }
        qs = _question_set(release_reader, env["release_id"], qs_cache)
        if qs is not None:
            case["question_set"] = qs
        try:
            existing = _existing(store, case["case_id"])
            if existing is None:
                store.publish_case(case)
                rep.cases_published += 1
            elif existing["version"] == 1 and _core(existing) == _core(case):
                rep.cases_existing += 1
            elif existing["version"] == 1 and existing["review_status"] == "PENDING":
                rep.skipped_diff += 1                      # 설정이 바뀌어 결과가 달라졌다. 기존 사례를 건드리지 않는다.
            else:
                rep.cases_existing += 1                    # 검토가 끝났거나 새 version이 있다
        except BoundaryError as e:
            rep.errors.append({"turn": deid.pseudonym("case", tid), "error": f"{e.code}"})
            if e.retryable:
                continue                                    # 표시하지 않고 다음 실행에서 다시
            log.mark(sid, tid, "PUBLISH_FAILED", cfg_hash, clock())
            continue
        log.mark(sid, tid, "DONE_CASE", cfg_hash, clock())

    days = cfg["retention"].get("raw_days")
    if days is not None:
        cutoff = fmt_ts(parse_ts(clock()) - timedelta(days=days))
        rep.purged_raw = log.purge_received_before(cutoff)

    if evaluate_rollback and planner is not None:
        rep.rollback = planner.evaluate()
    return rep


def _existing(store: FailureStoreImpl, case_id: str) -> Optional[dict]:
    try:
        return store.get_case(case_id, version=1)
    except BoundaryError as e:
        if e.code == "NOT_FOUND":
            return None
        raise


def _core(c: dict) -> str:
    return sha256_hash({k: c[k] for k in c if k != "ext"} | {"ext": c.get("ext")})


def _import(path: str):
    mod, _, fn = path.partition(":")
    return getattr(importlib.import_module(mod), fn)


def build_from_config(cfg: dict, env: dict, *, clock: Clock = utc_now):
    policy = DeidPolicy.from_config(cfg["deid"])
    salt = env.get(cfg["deid"].get("salt_env", "OPS_DEID_SALT"), "")
    if not salt:
        raise ValueError(f"의사 식별자 salt 환경 변수({cfg['deid'].get('salt_env', 'OPS_DEID_SALT')})가 없다")
    log = LogStore.sqlite(cfg["storage"]["log_db"], clock=clock)
    store = FailureStoreImpl.sqlite(cfg["storage"]["failure_db"])
    deid = Deidentifier(policy, salt, log.grant_deidentifier_view())
    detectors = build_detectors(cfg["detection"]["detectors"])
    classifier = Classifier(cfg["classification"])
    planner = None
    rb = cfg.get("rollback")
    rr = cfg.get("release_reader")
    reader = _import(rr["factory"])(**(rr.get("args") or {})) if rr else None
    if rb and rr:
        gate = _import(cfg["gate_service"]["factory"])(**(cfg["gate_service"].get("args") or {})) if cfg.get("gate_service") else None
        planner = RollbackPlanner(store=store, log=log, release_reader=reader, gate=gate, policy=rb, clock=clock)
    return log, store, deid, detectors, classifier, planner, reader


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ops-feedback 주기 잡 한 번 실행")
    ap.add_argument("--config", required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--reprocess", action="store_true", help="처리 표시를 모두 지우고 다시 처리(결과는 같다)")
    ap.add_argument("--retry-deid-failed", action="store_true")
    ap.add_argument("--no-rollback", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    log, store, deid, detectors, classifier, planner, reader = build_from_config(cfg, dict(os.environ))
    rep = run_once(cfg=cfg, log=log, store=store, deid=deid, detectors=detectors, classifier=classifier, planner=planner,
                   limit=a.limit, reprocess=a.reprocess, retry_deid_failed=a.retry_deid_failed,
                   evaluate_rollback=not a.no_rollback, release_reader=reader)
    print(json.dumps(rep.as_dict(), ensure_ascii=False, sort_keys=True))
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())

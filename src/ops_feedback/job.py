"""주기 잡: 비식별 -> 실패 탐지 -> 분류 -> 사례 게시(-> 롤백 평가). 한 번 실행하고 끝난다(D39).

멱등: 같은 Trace를 두 번 처리해도 같은 사례가 나온다. case_id, trace ref, created_at이 모두 입력에서 결정된다.
명령: ops-feedback-job --config config/job.example.yaml [--limit N] [--reprocess] [--retry-deid-failed] [--no-rollback]
"""
from __future__ import annotations

from boundary_contracts import read_secret

from .storage import open_failure_store, open_log_store
import argparse
import importlib
import json
import os
import sys
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from boundary_contracts.canonical import sha256_hash
from boundary_contracts.errors import BoundaryError

from .clock import Clock, fmt_ts, parse_ts, utc_now
from .config import FOREVER, RETENTION_DEFAULTS, load_config
from .deid import DeidError, Deidentifier, DeidPolicy, check_salt
from .detect import Classifier, Detector, build_detectors, pause_reason_of
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
    minimized_raw: int = 0
    purged_cases: int = 0
    purged_failed_raw: int = 0
    purged_aux: int = 0
    warnings: list = field(default_factory=list)       # 사용자 결정 전의 기본값이 켜져 있다는 알림(오류 아님, 종료 코드에 영향 없음)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _question_set(reader, release_id: str, cache: dict) -> Optional[dict]:
    """source_release_id의 ReleaseManifest(I4)에서 문항 세트 ref. 읽을 수 없으면 None(필드 생략, 1.0 호환).

    한 번 UNAVAILABLE(시간 초과 포함)이면 이번 실행에서는 다른 릴리스도 부르지 않는다: 상류가 멈춘 채 릴리스마다 읽기 timeout을 기다리지 않게
    한다(원칙 4). 알려진 위험: 일시 장애 때 만든 사례는 문항 세트 없이 게시되고, version 1은 나중에 고쳐 쓰지 않는다(docs/boundary.md)."""
    if reader is None:
        return None
    if "__down__" in cache:
        return None
    if release_id not in cache:
        try:
            qs = reader.get_release(release_id).get("question_set")
            cache[release_id] = dict(qs) if isinstance(qs, dict) else None
        except BoundaryError as e:
            if e.retryable:
                cache["__down__"] = e.code
            cache[release_id] = None
        except (AttributeError, TypeError, KeyError, ValueError):     # 깨진 응답은 지어내지 않고 생략한다(원칙 10)
            cache[release_id] = None
    return cache[release_id]


DEFAULT_BATCH_SIZE = 500 # 근거: 봉투 최대 8 MiB를 한 번에 올리는 양을 제한하는 임의값, 측정 안 함(ADR 0008).     # 한 번에 메모리에 올리는 미처리 원본 수(봉투 하나 최대 8 MiB). 설정 job.batch_size로 바꾼다


def _pending_rows(deid: Deidentifier, limit: Optional[int], batch_size: int):
    """미처리 원본을 seq 순으로 batch_size씩 읽어 하나씩 낸다. 표시하지 않고 넘어간 행(재시도 대상)을 다시 읽지 않도록 seq로 이어 읽는다."""
    after, left = 0, limit
    while left is None or left > 0:
        rows = deid.pending(batch_size if left is None else min(batch_size, left), after)
        if not rows:
            return
        for r in rows:
            yield r
        after = max(r["seq"] for r in rows)
        if left is not None:
            left -= len(rows)


def retention_warnings(cfg: dict, policy=None) -> list:
    """명시적 무기한("forever")이나 처리 뒤 전체 보관 같은, 보존이 길게 열려 있는 설정과 값 검증 없는 keep 키를 알린다."""
    out = []
    ret = cfg.get("retention") or {}
    for key in RETENTION_DEFAULTS:
        if ret.get(key, RETENTION_DEFAULTS[key]) == FOREVER:
            out.append(f"RETENTION_FOREVER: retention.{key}가 forever라 무기한 보관된다(U4 기본은 {RETENTION_DEFAULTS[key]}일)")
    if ret.get("failed_raw_days") == FOREVER:       # 명시했을 때만. 적지 않으면 raw_days를 따르므로 위의 raw_days 경고가 그 경우를 덮는다
        out.append("RETENTION_FOREVER: retention.failed_raw_days가 forever라 재시도 대상 원본이 무기한 보관된다")
    if ret.get("raw_after_processing", "keep") == "keep" and ret.get("raw_days", 30) != FOREVER:
        pass       # 보관 기간 안에서는 전체 보관이 기본이다. 줄이려면 raw_after_processing: minimize
    if policy is not None and policy.unvalidated_keep_keys():
        out.append("DEID_KEEP_KEYS_UNVALIDATED: 값 검증 패턴이 없는 keep_string_keys: " + ",".join(policy.unvalidated_keep_keys()))
    return out


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

    rep.warnings.extend(retention_warnings(cfg, deid.policy))
    for row in _pending_rows(deid, limit, int((cfg.get("job") or {}).get("batch_size", DEFAULT_BATCH_SIZE))):    # 원본을 읽는 유일한 경로
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
        qs = _question_set(release_reader, env["release_id"], qs_cache)
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
        pr = pause_reason_of(res.trace)
        if pr is not None:
            case["ext"]["ops-feedback"]["pause_reason"] = pr          # 식별정보가 아닌 열거값(ADR 0014)
        if qs is not None:
            case["question_set"] = qs
        try:
            existing = _existing(store, case["case_id"])
            if existing is None:
                try:
                    store.publish_case(case)
                except BoundaryError as e:
                    if e.code != "FAILED_PRECONDITION":
                        raise
                    # 방금 저장한 trace를 같은 시각에 도는 admin purge(고아 trace 정리)가 지웠을 수 있다. trace를 다시 저장하고 한 번만 다시 시도한다.
                    store.put_trace(trace_ref, res.trace)
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

    ret = cfg["retention"]
    now = parse_ts(clock())

    def cutoff_of(key: str):
        d = ret[key] if key in ret else RETENTION_DEFAULTS[key]
        return None if d == FOREVER else fmt_ts(now - timedelta(days=d))

    raw_cut = cutoff_of("raw_days")
    if raw_cut is not None:
        # 처리 표시가 있는 원본만 지운다(기본). 잡이 raw_days보다 오래 멈춰도 미처리 원본이 처리 전에 사라지지 않는다.
        # 대신 보관 기한을 넘기고도 처리되지 못한 원본이 있으면 경보(보고서 warnings)로 알린다.
        rep.purged_raw = log.purge_received_before(raw_cut, processed_only=not ret.get("purge_unprocessed", False))
        old = deid.pending(1)
        if old and old[0]["received_at"] < raw_cut and not ret.get("purge_unprocessed", False):
            rep.warnings.append("UNPROCESSED_RAW_PAST_RETENTION: 보관 기한을 넘긴 원본이 아직 처리되지 않았다(지우지 않았다). 잡이 멈췄거나 처리가 막혔는지 확인한다")
    # 재시도 대상 원본: failed_raw_days를 명시했을 때만 별도 기한, 아니면 raw_days를 그대로 따른다(raw_days가 forever면 forever)
    failed_cut = cutoff_of("failed_raw_days") if "failed_raw_days" in ret else raw_cut
    if failed_cut is not None:
        rep.purged_failed_raw = log.purge_failed_before(failed_cut)      # 재시도 대상은 별도 기한 뒤에만 지운다
    if ret.get("raw_after_processing", "keep") == "minimize":
        rep.minimized_raw = log.minimize_processed()
    cases_cut, metrics_cut = cutoff_of("cases_days"), cutoff_of("metrics_days")
    if cases_cut is not None:
        r = store.purge_cases_created_before(cases_cut, include_confirmed=ret.get("cases_purge_confirmed", True))
        rep.purged_cases = r["cases"]
    if metrics_cut is not None:
        rep.purged_aux = store.purge_aux_before(metrics_cut)

    if evaluate_rollback and planner is not None:
        rep.rollback = _evaluate_rollback(planner, cfg, rep)
    return rep


DEFAULT_ROLLBACK_DEADLINE = 60.0 # 근거 기록 없음(임의 보수값, ADR 0008).     # 롤백 평가 전체(I4 읽기 여러 번 + I5 전달)의 상한(초). 기준 값이 아니라 대기 상한이다(FAULT_POLICY 4)


def _evaluate_rollback(planner: RollbackPlanner, cfg: dict, rep: JobReport) -> dict:
    """롤백 평가를 상한 안에서 한다. 이웃(I4/I5)이 멈추거나 실패해도 잡은 끝나고, 실패는 보고서와 종료 코드(1)에 드러난다(원칙 9).

    클라이언트의 읽기 timeout(boundary_contracts 기본 10초)은 호출 한 번의 소켓 대기라 응답을 조금씩 흘리는 상류에서는 합이 길어질 수 있다.
    그래서 평가 전체에도 상한(`rollback.deadline_seconds`, 기본 60초)을 둔다. 넘으면 평가 스레드는 버리고(데몬) 잡이 끝난다.
    이미 저장된 요청·영수증은 그대로이고 다음 실행이 같은 request_id로 이어 간다."""
    deadline = float((cfg.get("rollback") or {}).get("deadline_seconds", DEFAULT_ROLLBACK_DEADLINE))
    box: dict = {}

    def run():
        try:
            box["out"] = planner.evaluate()
        except BaseException as e:           # noqa: BLE001 스레드 밖으로 옮겨 처리한다
            box["exc"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(deadline)
    if t.is_alive():
        rep.errors.append({"stage": "rollback", "error": "DEADLINE_EXCEEDED"})
        return {"action": "ERROR", "error": "DEADLINE_EXCEEDED"}
    exc = box.get("exc")
    if exc is not None:
        code = exc.code if isinstance(exc, BoundaryError) else "INTEGRITY_ERROR"     # 형식이 다른 응답(KeyError 등)은 값을 지어내지 않고 실패로
        rep.errors.append({"stage": "rollback", "error": code})
        return {"action": "ERROR", "error": code}
    out = box["out"]
    if out.get("submit_error"):
        rep.errors.append({"stage": "rollback_submit", "error": out["submit_error"]})
    return out


def _existing(store: FailureStoreImpl, case_id: str) -> Optional[dict]:
    try:
        return store.get_case(case_id, version=1)
    except BoundaryError as e:
        if e.code == "NOT_FOUND":
            return None
        raise


def _core(c: dict) -> str:
    return sha256_hash({k: c[k] for k in c if k != "ext"} | {"ext": c.get("ext")})


def _resolve_args(args, env: dict, path: str = "args"):
    """설정의 {secret: 이름} 또는 {env: 이름}을 read_secret(이름)으로 푼다(중첩 포함). 비밀 값을 설정 파일에 두지 않는다."""
    if isinstance(args, dict):
        if set(args) in ({"secret"}, {"env"}):
            name = args.get("secret", args.get("env"))
            if not isinstance(name, str) or not name:
                raise ValueError(f"설정 {path}: 비밀 이름은 비어 있지 않은 문자열이어야 한다")
            val = read_secret(name, environ=env)      # NAME_FILE 우선, 없으면 NAME
            if val is None:
                raise ValueError(f"비밀 {name}가 없다(NAME_FILE 또는 NAME)")
            return val
        return {k: _resolve_args(v, env, f"{path}.{k}") for k, v in args.items()}
    if isinstance(args, list):
        return [_resolve_args(v, env, f"{path}[{i}]") for i, v in enumerate(args)]
    return args


def _import(path: str):
    mod, _, fn = path.partition(":")
    return getattr(importlib.import_module(mod), fn)


def build_from_config(cfg: dict, env: dict, *, clock: Clock = utc_now):
    policy = DeidPolicy.from_config(cfg["deid"])
    salt_name = cfg["deid"].get("salt_env", "OPS_DEID_SALT")
    salt = read_secret(salt_name, environ=env)          # NAME_FILE 우선, 없으면 NAME
    if not salt:
        raise ValueError(f"의사 식별자 salt 비밀({salt_name}_FILE 또는 {salt_name})이 없다")
    check_salt(salt)          # 짧은 salt는 저장소를 열기 전에 거부한다(ADR 0017)
    log = open_log_store(env, cfg["storage"]["log_db"], clock=clock)
    store = open_failure_store(env, cfg["storage"]["failure_db"])
    deid = Deidentifier(policy, salt, log.grant_deidentifier_view())
    detectors = build_detectors(cfg["detection"]["detectors"])
    classifier = Classifier(cfg["classification"])
    planner = None
    rb = cfg.get("rollback")
    rr = cfg.get("release_reader")
    reader = _import(rr["factory"])(**_resolve_args(rr.get("args") or {}, env, "release_reader.args")) if rr else None
    if rb and rr:
        gate = _import(cfg["gate_service"]["factory"])(**_resolve_args(cfg["gate_service"].get("args") or {}, env, "gate_service.args")) if cfg.get("gate_service") else None
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
    # DB가 무응답이면 문장·접속 상한(OPS_DB_*_TIMEOUT) 안에 BoundaryError(UNAVAILABLE)로 끝난다. 무한 대기하지 않고 종료 코드 2로 끝낸다(스택 트레이스 없이 코드만).
    try:
        log, store, deid, detectors, classifier, planner, reader = build_from_config(cfg, dict(os.environ))
        rep = run_once(cfg=cfg, log=log, store=store, deid=deid, detectors=detectors, classifier=classifier, planner=planner,
                       limit=a.limit, reprocess=a.reprocess, retry_deid_failed=a.retry_deid_failed,
                       evaluate_rollback=not a.no_rollback, release_reader=reader)
    except BoundaryError as e:
        print(json.dumps({"error": e.code, "message": e.message}, ensure_ascii=False), file=sys.stderr)
        return 2
    for w in rep.warnings:
        print("경고: " + w, file=sys.stderr)
    print(json.dumps(rep.as_dict(), ensure_ascii=False, sort_keys=True))
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())

"""주기 잡의 조립: 비식별 -> 실패 탐지 -> 분류 -> 사례 조립 -> 게시(-> 롤백 평가). 한 번 실행하고 끝난다(D39).
어느 컴포넌트의 출력을 어느 컴포넌트의 입력에 넘길지가 이 파일의 몫이다(구조 규칙 5). 업무 규칙은 컴포넌트에 있다.

멱등: 같은 Trace를 두 번 처리해도 같은 사례가 나온다. case_id, trace ref, created_at이 모두 입력에서 결정된다.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from boundary_contracts.canonical import sha256_hash
from boundary_contracts.errors import BoundaryError

from ..components import case_builder, case_reconciler, job_advisor, retention_planner
from ..components.deidentifier import DeidError, Deidentifier
from ..components.failure_case_store import FailureCaseStore
from ..components.failure_classifier import FailureClassifier
from ..components.failure_detector import FailureDetector
from ..components.link_release_read import ReleaseLink
from .clock import Clock, utc_now
from .log_store import LogStore
from .rollback_flow import RollbackPlanner

DEFAULT_BATCH_SIZE = 500 # 근거: 봉투 최대 8 MiB를 한 번에 올리는 양을 제한하는 임의값, 측정 안 함(ADR 0008).     # 한 번에 메모리에 올리는 미처리 원본 수(봉투 하나 최대 8 MiB). 설정 job.batch_size로 바꾼다
DEFAULT_ROLLBACK_DEADLINE = 60.0 # 근거 기록 없음(임의 보수값, ADR 0008).     # 롤백 평가 전체(I4 읽기 여러 번 + I5 전달)의 상한(초). 기준 값이 아니라 대기 상한이다(FAULT_POLICY 4)


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


def _pending_rows(view, limit: Optional[int], batch_size: int):
    """미처리 원본을 seq 순으로 batch_size씩 읽어 하나씩 낸다. 표시하지 않고 넘어간 행(재시도 대상)을 다시 읽지 않도록 seq로 이어 읽는다."""
    after, left = 0, limit
    while left is None or left > 0:
        rows = view.read_unprocessed(batch_size if left is None else min(batch_size, left), after)
        if not rows:
            return
        for r in rows:
            yield r
        after = max(r["seq"] for r in rows)
        if left is not None:
            left -= len(rows)


def _existing(store: FailureCaseStore, case_id: str) -> Optional[dict]:
    try:
        return store.get_case(case_id, version=1)
    except BoundaryError as e:
        if e.code == "NOT_FOUND":
            return None
        raise


def run_once(*, cfg: dict, log: LogStore, store: FailureCaseStore, deid: Deidentifier, pseudonymize: Callable[[str, str], str],
             detector: FailureDetector, classifier: FailureClassifier, planner: Optional[RollbackPlanner] = None, clock: Clock = utc_now,
             limit: Optional[int] = None, reprocess: bool = False, retry_deid_failed: bool = False,
             evaluate_rollback: bool = True, release_reader=None,
             rollback_wait: Callable[[threading.Event, float], bool] = lambda done, secs: done.wait(secs)) -> JobReport:
    rep = JobReport()
    release_link = ReleaseLink(release_reader) if release_reader is not None else None
    view = log.grant_deidentifier_view()          # 원본 읽기 권한을 비식별 단계에 넘기는 유일한 지점(tests/test_raw_access.py)
    cfg_hash = sha256_hash({"deid": deid.policy.fingerprint(), "detection": cfg["detection"], "classification": cfg["classification"]})
    if reprocess:
        log.reset_processing(None)
    elif retry_deid_failed:
        log.reset_processing("DEID_FAILED")

    rep.warnings.extend(job_advisor.advise({"retention": cfg.get("retention") or {}, "unvalidated_keep_keys": deid.policy.unvalidated_keep_keys()})["warnings"])
    for row in _pending_rows(view, limit, int((cfg.get("job") or {}).get("batch_size", DEFAULT_BATCH_SIZE))):    # 원본을 읽는 유일한 경로
        env = row["envelope"]
        sid, tid = env["session_id"], env["turn_id"]
        rep.processed += 1
        try:
            res = deid.deidentify({"envelope": env})
        except DeidError as e:
            log.mark(sid, tid, "DEID_FAILED", cfg_hash, clock())
            rep.deid_failed += 1
            rep.errors.append({"turn": pseudonymize("case", tid), "error": str(e)})
            continue
        report = detector.detect({"deid_trace": res["trace"]})
        if not report["detections"]:
            log.mark(sid, tid, "NO_FAILURE", cfg_hash, clock())
            rep.no_failure += 1
            continue
        cls = classifier.classify({"detections": report["detections"]})
        qs = release_link.question_set_of(env["release_id"]) if release_link is not None else None
        req = {"release_id": env["release_id"], "runtime_contract_version": env["runtime_contract_version"], "received_at": row["received_at"],
               "session_pseudonym": pseudonymize("case", sid), "turn_pseudonym": pseudonymize("case", tid), "deid": res,
               "policy_id": deid.policy.policy_id, "classification": cls, "detections": report["detections"]}
        if "pause_reason" in report:
            req["pause_reason"] = report["pause_reason"]
        if qs is not None:
            req["question_set"] = qs
        built = case_builder.build(req)
        case, trace_ref = built["case"], built["trace_ref"]
        store.put_trace(trace_ref, built["trace"])
        try:
            decision = case_reconciler.reconcile({"existing": _existing(store, case["case_id"]), "candidate": case})["action"]
            if decision == "PUBLISH":
                try:
                    store.publish_case(case)
                except BoundaryError as e:
                    if e.code != "FAILED_PRECONDITION":
                        raise
                    # 방금 저장한 trace를 같은 시각에 도는 admin purge(고아 trace 정리)가 지웠을 수 있다. trace를 다시 저장하고 한 번만 다시 시도한다.
                    store.put_trace(trace_ref, built["trace"])
                    store.publish_case(case)
                rep.cases_published += 1
            elif decision == "SKIPPED_DIFF":
                rep.skipped_diff += 1                      # 설정이 바뀌어 결과가 달라졌다. 기존 사례를 건드리지 않는다.
            else:
                rep.cases_existing += 1
        except BoundaryError as e:
            rep.errors.append({"turn": pseudonymize("case", tid), "error": f"{e.code}"})
            if e.retryable:
                continue                                    # 표시하지 않고 다음 실행에서 다시
            log.mark(sid, tid, "PUBLISH_FAILED", cfg_hash, clock())
            continue
        log.mark(sid, tid, "DONE_CASE", cfg_hash, clock())

    ret = cfg["retention"]
    cut = retention_planner.plan({"now": clock(), "retention": ret})
    if cut["raw_cutoff"] is not None:
        # 처리 표시가 있는 원본만 지운다(기본). 잡이 raw_days보다 오래 멈춰도 미처리 원본이 처리 전에 사라지지 않는다.
        # 대신 보관 기한을 넘기고도 처리되지 못한 원본이 있으면 경보(보고서 warnings)로 알린다.
        rep.purged_raw = log.purge_received_before(cut["raw_cutoff"], processed_only=not cut["purge_unprocessed"])
        old = view.read_unprocessed(1)
        rep.warnings.extend(job_advisor.advise({"retention": {}, "raw_cutoff": cut["raw_cutoff"], "purge_unprocessed": cut["purge_unprocessed"],
                                                "oldest_unprocessed_received_at": old[0]["received_at"] if old else None})["warnings"])
    if cut["failed_raw_cutoff"] is not None:
        rep.purged_failed_raw = log.purge_failed_before(cut["failed_raw_cutoff"])      # 재시도 대상은 별도 기한 뒤에만 지운다
    if cut["minimize_raw"]:
        rep.minimized_raw = log.minimize_processed()
    if cut["cases_cutoff"] is not None:
        rep.purged_cases = store.purge_cases_created_before(cut["cases_cutoff"], include_confirmed=cut["cases_purge_confirmed"])["cases"]
    if cut["metrics_cutoff"] is not None:
        rep.purged_aux = store.purge_aux_before(cut["metrics_cutoff"])

    if evaluate_rollback and planner is not None:
        rep.rollback = _evaluate_rollback(planner, cfg, rep, rollback_wait)
    return rep


def _evaluate_rollback(planner: RollbackPlanner, cfg: dict, rep: JobReport, wait: Callable[[threading.Event, float], bool]) -> dict:
    """롤백 평가를 상한 안에서 한다. 이웃(I4/I5)이 멈추거나 실패해도 잡은 끝나고, 실패는 보고서와 종료 코드(1)에 드러난다(원칙 9).

    클라이언트의 읽기 timeout(boundary_contracts 기본 10초)은 호출 한 번의 소켓 대기라 응답을 조금씩 흘리는 상류에서는 합이 길어질 수 있다.
    그래서 평가 전체에도 상한(`rollback.deadline_seconds`, 기본 60초)을 둔다. 넘으면 평가 스레드는 버리고(데몬) 잡이 끝난다.
    이미 저장된 요청·영수증은 그대로이고 다음 실행이 같은 request_id로 이어 간다.

    기다림은 `wait(끝남 신호, 상한 초)`로 주입한다(U422, ADR 0027): 운영은 Event.wait(실제 시간), 시험은 즉시 False를 돌려 '상한이 지났다'를 일으킨다."""
    deadline = float((cfg.get("rollback") or {}).get("deadline_seconds", DEFAULT_ROLLBACK_DEADLINE))
    box: dict = {}

    done = threading.Event()

    def run():
        try:
            box["out"] = planner.evaluate()
        except BaseException as e:           # noqa: BLE001 스레드 밖으로 옮겨 처리한다
            box["exc"] = e
        finally:
            done.set()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    if not wait(done, deadline):
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

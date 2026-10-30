"""입출력 예시를 만든다: 가짜 개인정보가 든 봉투 -> 잡 -> 비식별 FailureCase와 trace. 실행: python scripts/make_examples.py"""
import json, sys
from pathlib import Path
sys.path.insert(0, "tests")
from conftest import envelope, pii_trace, base_config
from ops_feedback.components.deidentifier import Deidentifier, DeidPolicy
from ops_feedback.components.failure_case_store import FailureCaseStore
from ops_feedback.components.failure_classifier import FailureClassifier
from ops_feedback.components.failure_detector import FailureDetector
from ops_feedback.wiring.build import make_pseudonymizer
from ops_feedback.wiring.log_store import LogStore
from ops_feedback.wiring.pipeline import run_once

cfg = base_config()
log, store = LogStore.memory(clock=lambda: "2026-10-14T00:00:01Z"), FailureCaseStore.memory()
env = envelope(pii_trace("s-001", "t-004"), release_id="rel-0002")
log.send(env)
ps = make_pseudonymizer("example-salt")
run_once(cfg=cfg, log=log, store=store, deid=Deidentifier(DeidPolicy.from_config(cfg["deid"]), ps), pseudonymize=ps,
         detector=FailureDetector(cfg["detection"]["detectors"]), classifier=FailureClassifier(cfg["classification"]), clock=lambda: "2026-10-15T00:00:00Z")
(case,), _ = store.list_cases()
out = Path("examples")
def w(n, o): (out / n).write_text(json.dumps(o, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
w("input.TraceEnvelope.json", env)
w("output.FailureCase.json", case)
w("output.deidentified_trace.json", store.get_trace(case["deidentified_trace_ref"]))

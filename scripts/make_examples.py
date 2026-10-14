"""입출력 예시를 만든다: 가짜 개인정보가 든 봉투 -> 잡 -> 비식별 FailureCase와 trace. 실행: python scripts/make_examples.py"""
import json, sys
from pathlib import Path
sys.path.insert(0, "tests")
from conftest import envelope, pii_trace, base_config
from ops_feedback.deid import Deidentifier, DeidPolicy
from ops_feedback.detect import Classifier, build_detectors
from ops_feedback.failurestore import FailureStoreImpl
from ops_feedback.job import run_once
from ops_feedback.logstore import LogStore

cfg = base_config()
log, store = LogStore.memory(clock=lambda: "2026-10-14T00:00:01Z"), FailureStoreImpl.memory()
env = envelope(pii_trace("s-001", "t-004"), release_id="rel-0002")
log.send(env)
deid = Deidentifier(DeidPolicy.from_config(cfg["deid"]), "example-salt", log.grant_deidentifier_view())
run_once(cfg=cfg, log=log, store=store, deid=deid, detectors=build_detectors(cfg["detection"]["detectors"]),
         classifier=Classifier(cfg["classification"]), clock=lambda: "2026-10-15T00:00:00Z")
(case,), _ = store.list_cases()
out = Path("examples")
def w(n, o): (out / n).write_text(json.dumps(o, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
w("input.TraceEnvelope.json", env)
w("output.FailureCase.json", case)
w("output.deidentified_trace.json", store.get_trace(case["deidentified_trace_ref"]))

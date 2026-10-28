"""contract-ready/examples/valid/ 의 값을 실제 코드(비식별 처리기, 잡, 검토 큐, 서버)와 리포의 설정 예시에서 만든다.
사용(리포 루트에서): .venv/bin/python docs/change-requests/contract-ready/generate_examples.py
비밀 값은 쓰지 않는다: salt는 이 스크립트 안의 시험용 문자열이다. 실행하면 examples/valid/ 를 덮어쓴다(invalid는 validate.py가 valid에서 파생해 확인하고, 파일은 손으로 둔다)."""
import copy, http.client, json, sys, tempfile, threading
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "examples" / "valid"
sys.path.insert(0, str(ROOT / "tests"))
from conftest import envelope, make_trace, pii_trace          # noqa: E402
from boundary_contracts.errors import BoundaryError           # noqa: E402
from ops_feedback.api import make_server, parse_read_tokens   # noqa: E402
from ops_feedback.deid import Deidentifier, DeidPolicy        # noqa: E402
from ops_feedback.detect import Classifier, build_detectors   # noqa: E402
from ops_feedback.failurestore import FailureStoreImpl        # noqa: E402
from ops_feedback.job import run_once                         # noqa: E402
from ops_feedback.logstore import LogStore                    # noqa: E402
from ops_feedback.review import ReviewQueue                   # noqa: E402

SALT = "contract-ready-example-salt-0123456789abcdef"       # 시험용(32바이트 이상, ADR 0017)
cfg = yaml.safe_load((ROOT / "config/job.example.yaml").read_text("utf-8"))
NOW = "2026-10-29T00:00:00Z"


def w(name, obj):
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False) + "\n", "utf-8")


log, store = LogStore.memory(clock=lambda: "2026-10-28T23:00:00Z"), FailureStoreImpl.memory()
deid = Deidentifier(DeidPolicy.from_config(cfg["deid"]), SALT, log.grant_deidentifier_view())

# 1) 기본 실패 턴(서버 대체 문장)
log.send(envelope(pii_trace("s-001", "t-001"), release_id="rel-0002"))
# 2) 직원 호출 신호 + 정지 사유가 있는 턴(규칙 TURN_FAILURE_SIGNAL, 되묻기 한도)
sig = {"session_id": "s-002", "write_committed": "NOT_COMMITTED", "staff_alert": "DELIVERED", "pause_reason": "REASK_LIMIT"}
entry = {"input": {"kind": "UTTERANCE", "utterance": "혈압이 몇인지 모르겠어요", "intervention_decision": None},
         "handling": {"handled": "UTTERANCE"}, "question_bank_version": 1,
         "turn_result": {"failed_steps": [{"phase": "BEFORE_COMMIT", "step": "COMMIT"}]},
         "extract_output": None, "validation_violations": None, "staff_alert": None, "response_text": None,
         "staff_alert_delivery": "SUCCESS", "turn_failure_signal": sig, "intervention_result": None}
t2 = make_trace("s-002", "t-002",
                orchestrator_output={"turn_kind": None, "session_info_created": None, "log_entry": entry, "turn_failure_signal": sig,
                                     "intervention_result": None, "dispatch_called": True},
                log_entry=entry,
                persistence_commit_result={"session_id": "s-002", "status": "DB_ERROR", "write_outcome": "NOT_APPLIED", "revision": None, "detail": None})
log.send(envelope(t2, release_id="rel-0002"))

# 비식별 trace 예시는 잡 전에 비식별기를 직접 한 번 더 돌려 파일로 쓴다(잡이 저장하는 것과 같은 함수)
w("OpsFeedbackDeidentifiedTrace.json", deid.deidentify(envelope(pii_trace("s-001", "t-001"), release_id="rel-0002")).trace)
w("OpsFeedbackDeidentifiedTrace.failure-signal.json", deid.deidentify(envelope(t2, release_id="rel-0002")).trace)

rep = run_once(cfg=cfg, log=log, store=store, deid=deid, detectors=build_detectors(cfg["detection"]["detectors"]),
               classifier=Classifier(cfg["classification"]), clock=lambda: NOW)
w("OpsFeedbackJobResult.json", rep.as_dict())
cases, _ = store.list_cases(latest_only=False)
by_rule = {c["ext"]["ops-feedback"]["primary_rule"]: c for c in cases}
w("OpsFeedbackFailureCaseExt.json", by_rule["RESPONSE_FALLBACK"]["ext"]["ops-feedback"])
sig_case = by_rule["TURN_FAILURE_SIGNAL"]
w("OpsFeedbackFailureCaseExt.pause-reason.json", sig_case["ext"]["ops-feedback"])
rev = ReviewQueue(store, clock=lambda: NOW).review(sig_case["case_id"], "DISMISSED", "reviewer", reviewed_by="kim.reviewer")
w("OpsFeedbackFailureCaseExt.reviewed.json", rev["ext"]["ops-feedback"])

# 설정: 리포의 예시 파일 그대로 + 롤백 항목을 더한 것
w("OpsFeedbackJobConfig.json", cfg)
rb = copy.deepcopy(cfg)
rb["rollback"] = {"window_hours": 24, "min_turns": 100, "max_failure_rate": 0.05, "counted_review_statuses": ["PENDING", "CONFIRMED"],
                  "requested_by_role": "ops-feedback", "auto_submit": False}
rb["release_reader"] = {"factory": "boundary_contracts.clients:HttpReleaseReader",
                        "args": {"base_url": "http://release-gate:8080", "token": {"secret": "OPS_GATE_READ_TOKEN"}}}
w("OpsFeedbackJobConfig.rollback.json", rb)


class Down(LogStore):
    def ping(self):
        raise BoundaryError("UNAVAILABLE", "저장소 확인 실패")


def readyz(store_obj, **kw):
    srv = make_server(store_obj, "ing-tok", "127.0.0.1", 0, readyz_cache_seconds=0, **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
        c.request("GET", "/readyz")
        r = c.getresponse()
        return r.status, json.loads(r.read())
    finally:
        srv.shutdown()


tmp = Path(tempfile.mkdtemp())          # /readyz는 운영 구성(SQLite 파일)에서 얻는다
st, body = readyz(LogStore.sqlite(tmp / "l.db"), failures=FailureStoreImpl.sqlite(tmp / "f.db"), read_tokens=parse_read_tokens("synthetic-data=rt"))
assert st == 200
w("OpsFeedbackReadyz.json", body)
st, body = readyz(LogStore.sqlite(tmp / "l2.db"), retention_declared=False, require_reviewer_identity=False)
assert st == 200 and body["warnings"]
w("OpsFeedbackReadyz.warnings.json", body)
down = Down.__new__(Down)
down.__dict__.update(LogStore.sqlite(tmp / "l3.db").__dict__)
st, body = readyz(down)
assert st == 503
w("OpsFeedbackReadyz.not-ready.json", body)

# 서버 환경 변수와 빌드 인자: 이름과 값 모양의 예시(runbook, scripts/smoke.sh 기준). 비밀 값은 파일 경로만 적는다.
w("OpsFeedbackServerEnv.json", {
    "env": {"OPS_INGEST_TOKEN_FILE": "/run/secrets/ingest", "OPS_READ_TOKENS_FILE": "/run/secrets/read",
            "OPS_REVIEW_TOKENS_FILE": "/run/secrets/review", "OPS_LOG_DATABASE_URL_FILE": "/run/secrets/log_db_url",
            "OPS_FAILURE_DATABASE_URL_FILE": "/run/secrets/failure_db_url", "OPS_DEID_SALT_FILE": "/run/secrets/deid_salt",
            "OPS_PORT": "8080", "OPS_MAX_CONNECTIONS": "64", "OPS_RAW_RETENTION_DAYS": "30", "OPS_REVIEW_REQUIRE_USER": "1",
            "OPS_DB_SCHEMA": "verify"},
    "build_args": {"COMMIT": "f139ba6", "CONTRACTS_VERSION": "v2.1.1", "SURVEY_SCHEMA_VERSION": "1",
                   "TURN_ENGINE_VERSION": "none", "MODEL_RUNTIME_VERSION": "none"}})
print("wrote", len(list(OUT.glob("*.json"))), "files")

"""CR-007의 스키마 초안을 예시 문서에 대해 검증한다. 사용: .venv/bin/python docs/change-requests/schemas/validate.py
유효 예시(실제 코드·설정에서 얻은 것)는 통과해야 하고, 거부 예시는 정확히 지정한 이유로 거부되어야 한다. 하나라도 어긋나면 종료 코드 1."""
import copy, json, sys
from pathlib import Path
import yaml
from jsonschema import Draft202012Validator, FormatChecker

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EX = HERE / "examples"


def load(n): return json.loads((HERE / n).read_text("utf-8"))
def ex(n): return json.loads((EX / n).read_text("utf-8"))


cfg_example = yaml.safe_load((ROOT / "config/job.example.yaml").read_text("utf-8"))
cfg_rollback = copy.deepcopy(cfg_example)
cfg_rollback["rollback"] = {"window_hours": 24, "min_turns": 100, "max_failure_rate": 0.05, "counted_review_statuses": ["PENDING", "CONFIRMED"], "requested_by_role": "ops-feedback", "auto_submit": False}
cfg_rollback["release_reader"] = {"factory": "boundary_contracts.clients:HttpReleaseReader", "args": {"base_url": "http://release-gate:8080", "token": {"secret": "OPS_GATE_READ_TOKEN"}}}

def mut(base, f):
    d = copy.deepcopy(base); f(d); return d

trace = ex("valid.deidentified-trace.json")
cases = [
  # (스키마, 이름, 문서, 통과해야 하는가, 거부 이유로 기대하는 문자열)
  ("ops-feedback-job-config.schema.json", "config/job.example.yaml", cfg_example, True, None),
  ("ops-feedback-job-config.schema.json", "예시 + rollback + release_reader", cfg_rollback, True, None),
  ("ops-feedback-job-config.schema.json", "거부: retention 키 오타(raw_dayz)", mut(cfg_example, lambda d: d["retention"].update(raw_dayz=30)), False, "raw_dayz"),
  ("ops-feedback-job-config.schema.json", "거부: 최상위 모르는 키", mut(cfg_example, lambda d: d.update(rollbak={})), False, "rollbak"),
  ("ops-feedback-job-config.schema.json", "거부: REASK_STREAK에 threshold 없음", mut(cfg_example, lambda d: d["detection"]["detectors"][0]["rules"].append({"id": "REASK_STREAK", "params": {}})), False, "threshold"),
  ("ops-feedback-job-config.schema.json", "거부: retention.raw_days가 null", mut(cfg_example, lambda d: d["retention"].update(raw_days=None)), False, "None"),
  ("ops-feedback-job-config.schema.json", "거부: rollback.auto_submit이 문자열", mut(cfg_rollback, lambda d: d["rollback"].update(auto_submit="false")), False, "false"),
  ("ops-feedback-server-env.schema.json", "서버 env + ARG", {"env": {"OPS_INGEST_TOKEN_FILE": "/run/secrets/ingest", "OPS_LOG_DATABASE_URL_FILE": "/run/secrets/log_db_url", "OPS_FAILURE_DATABASE_URL_FILE": "/run/secrets/fail_db_url", "OPS_READ_TOKENS_FILE": "/run/secrets/read", "OPS_REVIEW_TOKENS_FILE": "/run/secrets/review", "OPS_PORT": "8080", "OPS_RAW_RETENTION_DAYS": "30", "OPS_REVIEW_REQUIRE_USER": "1", "OPS_DB_SCHEMA": "verify"},
     "build_args": {"COMMIT": "c9ea5dd", "CONTRACTS_VERSION": "v2.1.1", "SURVEY_SCHEMA_VERSION": "1", "TURN_ENGINE_VERSION": "v0.4.2", "MODEL_RUNTIME_VERSION": "v0.7.6"}}, True, None),
  ("ops-feedback-server-env.schema.json", "거부: 환경 변수 이름 오타", {"env": {"OPS_RAW_RETENTON_DAYS": "30"}}, False, "OPS_RAW_RETENTON_DAYS"),
  ("ops-feedback-server-env.schema.json", "거부: 모르는 ARG", {"env": {}, "build_args": {"COMMIT_SHA": "x"}}, False, "COMMIT_SHA"),
  ("ops-feedback-server-env.schema.json", "거부: OPS_DB_SCHEMA 값", {"env": {"OPS_DB_SCHEMA": "skip"}}, False, "skip"),
  ("ops-feedback-deidentified-trace.schema.json", "비식별 trace (실제 잡 출력)", trace, True, None),
  ("ops-feedback-deidentified-trace.schema.json", "examples/output.deidentified_trace.json (리포 기존 예시)", json.loads((ROOT / "examples/output.deidentified_trace.json").read_text("utf-8")), True, None),
  ("ops-feedback-deidentified-trace.schema.json", "거부: 원본 session_id가 남음", mut(trace, lambda d: d.update(session_id="s-1")), False, "s-1"),
  ("ops-feedback-deidentified-trace.schema.json", "거부: 발화 원문이 남음", mut(trace, lambda d: d["frontend_message"].update(utterance="저는 홍길동이고 010-1234-5678")), False, "홍길동"),
  ("ops-feedback-deidentified-trace.schema.json", "거부: 모르는 최상위 키", mut(trace, lambda d: d.update(patient_name="x")), False, "patient_name"),
  ("ops-feedback-failurecase-ext.schema.json", "ext[ops-feedback] (실제 잡 출력)", ex("valid.ext-ops-feedback.json"), True, None),
  ("ops-feedback-failurecase-ext.schema.json", "거부: primary_rule 없음", mut(ex("valid.ext-ops-feedback.json"), lambda d: d.pop("primary_rule")), False, "primary_rule"),
  ("ops-feedback-failurecase-ext.schema.json", "거부: pause_reason 열거 밖", mut(ex("valid.ext-ops-feedback.json"), lambda d: d.update(pause_reason="BORED")), False, "BORED"),
  ("ops-feedback-readyz.schema.json", "/readyz 200 (실제 서버 응답)", ex("valid.readyz-ready.json"), True, None),
  ("ops-feedback-readyz.schema.json", "/readyz 200 + warnings", mut(ex("valid.readyz-ready.json"), lambda d: d.update(warnings=["RAW_RETENTION_UNSET"])), True, None),
  ("ops-feedback-readyz.schema.json", "/readyz 503", {"status": "not-ready", "code": "UNAVAILABLE"}, True, None),
  ("ops-feedback-readyz.schema.json", "거부: ready인데 counters 없음", mut(ex("valid.readyz-ready.json"), lambda d: d.pop("counters")), False, "counters"),
  ("ops-feedback-readyz.schema.json", "거부: 모르는 warning", mut(ex("valid.readyz-ready.json"), lambda d: d.update(warnings=["X"])), False, "X"),
  ("ops-feedback-job-result.schema.json", "잡 stdout (실제 실행)", ex("valid.job-result.json"), True, None),
  ("ops-feedback-job-result.schema.json", "거부: processed 없음", mut(ex("valid.job-result.json"), lambda d: d.pop("processed")), False, "processed"),
  ("ops-feedback-job-result.schema.json", "거부: errors 항목에 원 turn_id", mut(ex("valid.job-result.json"), lambda d: d.update(errors=[{"turn": "t-1", "error": "X"}])), False, "t-1"),
]
def all_messages(errs):
    """oneOf/anyOf 안쪽(context)의 사유까지 모아 거부 이유를 확인한다."""
    for e in errs:
        yield e.message
        yield from all_messages(e.context)


bad = 0
for schema_name, name, doc, should_pass, why in cases:
    schema = load(schema_name)
    Draft202012Validator.check_schema(schema)
    errs = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(doc))
    if should_pass:
        ok = not errs
        msg = "통과" if ok else "예상 밖 거부: " + errs[0].message[:120]
    else:
        text = " ".join(all_messages(errs))
        ok = bool(errs) and why in text
        msg = "거부됨: " + (errs[0].message[:90] if errs else "(거부 안 됨)") + ("" if ok else f"  [기대한 이유 '{why}' 없음]")
    bad += not ok
    print(f"{'OK  ' if ok else 'FAIL'} {schema_name.replace('ops-feedback-', '').replace('.schema.json', ''):20} {name} -> {msg}")
print(f"\n{len(cases)}건 중 {len(cases) - bad}건 기대대로, {bad}건 어긋남")
sys.exit(1 if bad else 0)

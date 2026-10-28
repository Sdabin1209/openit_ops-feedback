"""전수 점검(head/docs/audit/ops-feedback.md) 지적의 고침 시험: 비식별 값 검증, 삭제·보존, 엄격 설정, 배치, 서버 한도, 검토자 신원, 스키마 분리."""
import copy
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import pytest
import yaml
from boundary_contracts.errors import BoundaryError

from conftest import base_config, envelope, make_trace, pii_trace
from ops_feedback import admin
from ops_feedback.api import JsonFormatter, make_server, parse_read_tokens
from ops_feedback.config import load_config
from ops_feedback.deid import Deidentifier, DeidPolicy
from ops_feedback.failurestore import FailureStoreImpl
from ops_feedback.job import run_once
from ops_feedback.logstore import LogStore
from ops_feedback.review import ReviewQueue

NOW = "2026-10-14T12:00:00Z"


def run(p, **kw):
    now = kw.pop("now", NOW)
    return run_once(cfg=p["cfg"], log=p["log"], store=p["store"], deid=p["deid"], detectors=p["detectors"], classifier=p["classifier"],
                    clock=lambda: now, **kw)


# ---------- C4: 비식별 값 검증 ----------

def _deid(cfg):
    return Deidentifier(DeidPolicy.from_config(cfg["deid"]), "salt", None)


def _trace_with(**extract_output):
    t = make_trace("s-1", "t-1")
    t["extract_output"] = {"values": [], "action": "NEXT", "next_item": "Q2", "reason": "x", "targets": [], "note": "x", **extract_output}
    return t


@pytest.mark.parametrize("field,bad", [("next_item", "홍길동 환자"), ("next_item", "Q2 홍길동"), ("action", "홍길동"), ("action", "next step")])
def test_identifier_fields_with_free_text_are_masked(field, bad):
    out = _deid(base_config()).deidentify(envelope(_trace_with(**{field: bad})))
    got = out.trace["extract_output"][field]
    assert got == "[MASKED]" and bad not in json.dumps(out.trace, ensure_ascii=False)
    assert out.stats["masked_by_pattern"] >= 1


def test_identifier_fields_with_identifier_shape_survive():
    out = _deid(base_config()).deidentify(envelope(_trace_with(action="NEXT", next_item="Q2_1")))
    assert out.trace["extract_output"]["next_item"] == "Q2_1" and out.trace["extract_output"]["action"] == "NEXT"


def test_violation_path_and_code_are_validated():
    t = make_trace("s-1", "t-1")
    t["validation_ok"] = False
    t["validation_violations"] = [{"code": "RANGE", "path": "values[0].홍길동", "message": "m"}, {"code": "RANGE", "path": "values[0].key", "message": "m"}]
    out = _deid(base_config()).deidentify(envelope(t))
    v = out.trace["validation_violations"]
    assert v[0]["path"] == "[MASKED]" and v[1]["path"] == "values[0].key" and v[0]["code"] == "RANGE"


def test_pattern_is_configurable_and_unvalidated_keys_are_reported():
    cfg = base_config()
    cfg["deid"]["keep_string_keys"] = cfg["deid"]["keep_string_keys"] + ["extra_key"]
    cfg["deid"]["keep_string_patterns"] = {"next_item": r"Q\d+"}
    pol = DeidPolicy.from_config(cfg["deid"])
    assert pol.unvalidated_keep_keys() == ["extra_key"]
    out = Deidentifier(pol, "s", None).deidentify(envelope(_trace_with(next_item="END")))
    assert out.trace["extract_output"]["next_item"] == "[MASKED]"          # 설정 패턴이 기본을 대체한다


# ---------- C4/M-4: 검토자 입력의 개인정보 패턴 ----------

@pytest.fixture
def case_store(pipeline):
    pipeline["log"].send(envelope(pii_trace("s-1", "t-1"), release_id="rel-0002"))
    run(pipeline)
    (c,), _ = pipeline["store"].list_cases()
    return pipeline["store"], c["case_id"]


@pytest.mark.parametrize("bad", ["연락처 010-1234-5678", "mail me a@b.co", "901231-1234567"])
def test_review_rejects_pii_patterns_in_expected(case_store, bad):
    store, cid = case_store
    q = ReviewQueue(store)
    with pytest.raises(BoundaryError) as e:
        q.review(cid, "CONFIRMED", "reviewer", expected={"action": "NEXT", "next_item": "END", "values": [], "note": bad}, expected_kind="LORA_A")
    assert e.value.code == "INVALID_RECORD" and bad not in str(e.value)
    with pytest.raises(BoundaryError):
        q.review(cid, "CONFIRMED", "reviewer", expected=bad, expected_kind="LORA_B")
    assert store.get_case(cid)["version"] == 1                                # 아무것도 게시되지 않았다


# ---------- U7: 검토자 신원 ----------

def test_review_requires_gateway_user_by_default(pipeline):
    pipeline["log"].send(envelope(pii_trace("s-1", "t-1"), release_id="rel-0002"))
    run(pipeline)
    (c,), _ = pipeline["store"].list_cases()
    s = make_server(LogStore.memory(), "ing", "127.0.0.1", 0, failures=pipeline["store"], read_tokens=parse_read_tokens("r=rt"),
                    review_tokens=parse_read_tokens("reviewer=vt"))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{s.server_address[1]}"

    def post(headers):
        rq = urllib.request.Request(f"{base}/v1/cases/{c['case_id']}/review", data=json.dumps({"review_status": "DISMISSED"}).encode(),
                                    headers={"Authorization": "Bearer vt", "Content-Type": "application/json", **headers})
        try:
            r = urllib.request.urlopen(rq)
            return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        assert post({})[0] == 403                                             # 신원 없음: 거부
        assert pipeline["store"].get_case(c["case_id"])["version"] == 1
        st, rec = post({"X-Openit-User": "kim.reviewer"})
        assert st == 201 and rec["ext"]["ops-feedback"]["reviewed_by"] == "kim.reviewer"
    finally:
        s.shutdown()


# ---------- U4/S2: 보존 설정은 null 거부, 기본값 적용 ----------

def _write(tmp_path, mutate):
    cfg = base_config()
    cfg["retention"] = {}
    mutate(cfg)
    f = tmp_path / "c.yaml"
    f.write_text(yaml.safe_dump(cfg))
    return load_config(f)


def test_retention_defaults_applied_when_omitted(tmp_path):
    assert _write(tmp_path, lambda c: None)["retention"] == {"raw_days": 30, "cases_days": 180, "metrics_days": 365}      # failed_raw_days는 기본값 없음(raw_days를 따른다)


@pytest.mark.parametrize("key", ["raw_days", "cases_days", "metrics_days"])
def test_retention_null_and_garbage_rejected(tmp_path, key):
    for bad in (None, 0, -1, "30", True):
        with pytest.raises(ValueError):
            _write(tmp_path, lambda c: c["retention"].update({key: bad}))


def test_explicit_forever_allowed_with_warning(tmp_path):
    from ops_feedback.job import retention_warnings
    cfg = _write(tmp_path, lambda c: c["retention"].update({"raw_days": "forever"}))
    assert any(w.startswith("RETENTION_FOREVER: retention.raw_days") for w in retention_warnings(cfg))


@pytest.mark.parametrize("val", ["false", "no", "False", 0, None])
def test_auto_submit_must_be_a_real_boolean(tmp_path, val):
    def m(c):
        c["rollback"] = {"window_hours": 1, "min_turns": 1, "max_failure_rate": 0.1, "counted_review_statuses": ["PENDING"],
                         "requested_by_role": "r", "auto_submit": val}
    with pytest.raises(ValueError):
        _write(tmp_path, m)
    from ops_feedback.rollback import RollbackPlanner
    with pytest.raises(ValueError):
        RollbackPlanner(store=None, log=None, release_reader=None, gate=None, policy={"auto_submit": val})


# ---------- C3: 삭제·최소화 ----------

def seed_old(p, n_fail=2, n_ok=1, sent="2026-01-01T00:00:00Z"):
    p["log"]._clock = lambda: sent       # 오래된 수신 시각
    for i in range(n_fail):
        p["log"].send(envelope(pii_trace("s-old", f"f{i}"), release_id="rel-0002"))
    for i in range(n_ok):
        p["log"].send(envelope(make_trace("s-old", f"o{i}"), release_id="rel-0002"))


def test_job_purges_processed_raw_cases_and_metrics_but_not_unprocessed(pipeline):
    seed_old(pipeline)
    pipeline["cfg"]["retention"] = {"raw_days": 30, "cases_days": 180, "metrics_days": 365}
    rep = run(pipeline, now="2026-10-14T12:00:00Z")
    assert rep.processed == 3 and rep.purged_raw == 3 and rep.purged_cases == 2          # 처리 끝난 것만
    assert pipeline["store"].list_cases(latest_only=False)[0] == []
    assert [w for w in rep.warnings if w.startswith("RETENTION")] == []


def test_unprocessed_raw_kept_and_alarmed(pipeline):
    pipeline["log"]._clock = lambda: "2026-01-01T00:00:00Z"
    pipeline["log"].send(envelope(make_trace("s-1", "t-1"), release_id="rel-0002"))
    pipeline["cfg"]["retention"] = {"raw_days": 30, "cases_days": "forever", "metrics_days": "forever"}
    pipeline["deid"].pending = lambda limit=None, after_seq=0: ([] if after_seq else pipeline["log"].grant_deidentifier_view().read_unprocessed(limit, after_seq))  # 이번 실행은 처리하지 못했다고 가정
    rep = run_once(cfg=pipeline["cfg"], log=pipeline["log"], store=pipeline["store"], deid=pipeline["deid"], detectors=[], classifier=pipeline["classifier"],
                   clock=lambda: "2026-10-14T12:00:00Z", limit=0)
    assert rep.purged_raw == 0 and pipeline["log"].counts_by_release() == {"rel-0002": 1}
    assert any(w.startswith("UNPROCESSED_RAW_PAST_RETENTION") for w in rep.warnings)


def test_minimize_keeps_dedupe_and_counts(pipeline):
    pipeline["log"].send(envelope(pii_trace("s-1", "t-1"), release_id="rel-0002"))
    pipeline["cfg"]["retention"] = {"raw_days": "forever", "cases_days": "forever", "metrics_days": "forever", "raw_after_processing": "minimize"}
    rep = run(pipeline)
    assert rep.minimized_raw == 1
    assert pipeline["log"].send(envelope(pii_trace("s-1", "t-1"), release_id="rel-0002"))["status"] == "DUPLICATE"      # 다시 ACCEPTED로 들어오지 않는다
    assert pipeline["log"].counts_by_release() == {"rel-0002": 1}
    assert pipeline["log"].reset_processing(None) == 0 and pipeline["deid"].pending() == []                              # 재처리 대상이 아니다


def test_admin_cli_delete_session_purge_and_minimize(tmp_path):
    ldb, fdb = str(tmp_path / "l.db"), str(tmp_path / "f.db")
    log, fail = LogStore.sqlite(ldb), FailureStoreImpl.sqlite(fdb)
    cfg = base_config()
    from ops_feedback.detect import Classifier, build_detectors
    deid = Deidentifier(DeidPolicy.from_config(cfg["deid"]), "cli-salt-0123456789abcdef-0123456789abcdef", log.grant_deidentifier_view())
    for sid in ("s-a", "s-b"):
        log.send(envelope(pii_trace(sid, "t-1"), release_id="rel-0002"))
    run_once(cfg=cfg, log=log, store=fail, deid=deid, detectors=build_detectors(cfg["detection"]["detectors"]),
             classifier=Classifier(cfg["classification"]), clock=lambda: NOW)
    assert len(fail.list_cases(latest_only=False)[0]) == 2
    env = {**os.environ, "OPS_DEID_SALT": "cli-salt-0123456789abcdef-0123456789abcdef"}
    base = [sys.executable, "-m", "ops_feedback.admin", "--log-db", ldb, "--failure-db", fdb]
    dry = subprocess.run(base + ["delete-session", "--session-id", "s-a", "--dry-run"], env=env, capture_output=True, text=True)
    assert dry.returncode == 0 and json.loads(dry.stdout)["traces"] == 1 and len(fail.list_cases(latest_only=False)[0]) == 2
    r = subprocess.run(base + ["delete-session", "--session-id", "s-a"], env=env, capture_output=True, text=True)
    assert r.returncode == 0 and json.loads(r.stdout) == {"cases": 1, "dry_run": False, "raw_rows": 1, "traces": 1}
    assert "s-a" not in r.stdout
    left = fail.list_cases(latest_only=False)[0]
    assert len(left) == 1 and fail.get_trace(left[0]["deidentified_trace_ref"])["session_id"] == admin.session_pseudonym("cli-salt-0123456789abcdef-0123456789abcdef", "s-b")
    assert log.send(envelope(pii_trace("s-a", "t-1"), release_id="rel-0002"))["status"] == "ACCEPTED"      # 삭제됐으니 새로 받는다
    nosalt = subprocess.run(base + ["delete-session", "--session-id", "s-b"], env={k: v for k, v in os.environ.items() if not k.startswith("OPS_")}, capture_output=True, text=True)
    assert nosalt.returncode == 2
    assert subprocess.run(base + ["purge"], env=env, capture_output=True, text=True).returncode == 2        # 기간 값이 없으면 거부(기본값 없음)
    p = subprocess.run(base + ["purge", "--cases-days", "1", "--include-confirmed"], env=env, capture_output=True, text=True)
    assert p.returncode == 0 and json.loads(p.stdout)["cases"] == {"cases": 0, "traces": 0}     # 방금 만든 사례는 아직 기한 전


def test_purge_cases_orphans_and_confirmed_rule(failure_store):
    from test_conformance_failurestore import case, TRACE
    failure_store.put_trace("tr-1", TRACE)
    failure_store.put_trace("tr-2", TRACE)
    failure_store.publish_case(case("fc-old", created_at="2026-01-01T00:00:00Z"))
    failure_store.publish_case(case("fc-conf", created_at="2026-01-01T00:00:00Z", deidentified_trace_ref="tr-2"))
    failure_store.publish_case(case("fc-conf", version=2, review_status="CONFIRMED", expected={"items": []}, created_at="2026-10-01T00:00:00Z", deidentified_trace_ref="tr-2"))
    assert failure_store.purge_cases_created_before("2026-06-01T00:00:00Z", include_confirmed=False) == {"cases": 1, "traces": 1}
    assert [c["case_id"] for c in failure_store.list_cases(latest_only=False)[0]] == ["fc-conf", "fc-conf"]       # 확정 사례는 기본 보호
    assert failure_store.purge_cases_created_before("2026-06-01T00:00:00Z", include_confirmed=True) == {"cases": 2, "traces": 1}
    assert failure_store.list_cases(latest_only=False)[0] == []


def test_purge_aux_before(failure_store):
    failure_store.put_aux("metric", "m1", {"metric_id": "m1", "window_end": "2025-01-01T00:00:00Z"})
    failure_store.put_aux("metric", "m2", {"metric_id": "m2", "window_end": "2026-10-01T00:00:00Z"})
    failure_store.put_aux("rollback_request", "rb1", {"request_id": "rb1", "requested_at": "2025-01-01T00:00:00Z"})
    assert failure_store.purge_aux_before("2026-01-01T00:00:00Z") == 2
    assert [m["metric_id"] for m in failure_store.list_aux("metric")] == ["m2"]


# ---------- G3: 배치 ----------

@pytest.mark.parametrize("bs", [1, 2, 500])
def test_batches_cover_everything_and_do_not_loop_on_retryable_rows(pipeline, bs):
    for i in range(5):
        pipeline["log"].send(envelope(make_trace("s", f"o{i}"), release_id="rel-0002", sent_at=NOW))
    pipeline["cfg"]["job"] = {"batch_size": bs}
    rep = run(pipeline)
    assert rep.processed == 5 and rep.no_failure == 5
    # 재시도 대상(표시하지 않고 넘어가는 행)이 있어도 같은 행을 계속 다시 읽지 않는다
    for i in range(3):
        pipeline["log"].send(envelope(pii_trace("s", f"f{i}"), release_id="rel-0002", sent_at=NOW))
    orig = pipeline["store"].publish_case

    def flaky(c):
        raise BoundaryError("UNAVAILABLE", "x")
    pipeline["store"].publish_case = flaky
    rep = run(pipeline)
    pipeline["store"].publish_case = orig
    assert rep.processed == 3 and len(rep.errors) == 3
    assert run(pipeline).cases_published == 3


def test_limit_is_respected_across_batches(pipeline):
    for i in range(5):
        pipeline["log"].send(envelope(make_trace("s", f"o{i}"), release_id="rel-0002"))
    pipeline["cfg"]["job"] = {"batch_size": 2}
    assert run(pipeline, limit=3).processed == 3


# ---------- A9/B10/B4/B5/A4: 서버 ----------

def _server(**kw):
    s = make_server(LogStore.memory(), "tok", "127.0.0.1", 0, **kw)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, s.server_address[1]


def test_readyz_result_is_cached_briefly():
    calls = {"n": 0}
    store = LogStore.memory()
    orig = store.ping
    store.ping = lambda: (calls.__setitem__("n", calls["n"] + 1), orig())[1]
    s = make_server(store, "tok", "127.0.0.1", 0, readyz_cache_seconds=60)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        for _ in range(20):
            urllib.request.urlopen(f"http://127.0.0.1:{s.server_address[1]}/readyz").read()
        assert calls["n"] == 1
    finally:
        s.shutdown()


def test_readyz_shows_counters_and_retention_warning():
    s, port = _server(retention_declared=False)
    try:
        body = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz").read())
        assert body["warnings"] == ["RAW_RETENTION_UNSET"] and body["counters"]["auth_rejected"] == 0
        rq = urllib.request.Request(f"http://127.0.0.1:{port}/v1/traces", data=b"{}", headers={"Authorization": "Bearer wrong"})
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(rq)
        # 캐시된 결과가 아니라 현재 카운터를 보여 준다
        assert json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz").read())["counters"]["auth_rejected"] == 1
    finally:
        s.shutdown()


def test_connection_cap_returns_503_instead_of_spawning_threads():
    s, port = _server(max_connections=2)
    held = []
    try:
        for _ in range(2):
            c = socket.create_connection(("127.0.0.1", port))
            c.sendall(b"POST /v1/traces HTTP/1.1\r\nAuthorization: Bearer tok\r\nContent-Length: 100\r\n\r\n")      # 본문을 덜 보내고 붙들고 있는 연결
            held.append(c)
        time.sleep(0.3)
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/healthz")
        assert c.getresponse().status == 503
    finally:
        for h in held:
            h.close()
        s.shutdown()


def test_deeply_nested_json_and_non_ascii_token_do_not_drop_the_connection():
    s, port = _server()
    try:
        deep = b"[" * 100000 + b"]" * 100000
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("POST", "/v1/traces", body=deep, headers={"Authorization": "Bearer tok", "Content-Type": "application/json"})
        assert c.getresponse().status == 400
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("POST", "/v1/traces", body=b"{}", headers={"Authorization": "Bearer 한글".encode("utf-8").decode("latin-1")})
        assert c.getresponse().status == 403
    finally:
        s.shutdown()


def test_json_log_formatter_escapes_quotes_and_newlines():
    import logging
    rec = logging.LogRecord("x", logging.INFO, "f", 1, 'GET /a"b\n{"lvl":"FAKE"}', (), None)
    line = JsonFormatter().format(rec)
    assert "\n" not in line and json.loads(line)["msg"] == 'GET /a"b\n{"lvl":"FAKE"}'


def test_sigterm_stops_the_server_cleanly(tmp_path):
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPS_")}
    env.update(OPS_INGEST_TOKEN="tok", OPS_LOG_DB=str(tmp_path / "l.db"), OPS_PORT=str(port))
    p = subprocess.Popen([sys.executable, "-m", "ops_feedback.api"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1).read()
            break
        except Exception:
            time.sleep(0.2)
    p.send_signal(signal.SIGTERM)
    out = p.communicate(timeout=10)[0]
    assert p.returncode == 0
    assert "RAW_RETENTION_UNSET" in out          # 보관 기간 미선언 경고가 시작 로그에 있다
    for line in out.strip().splitlines():
        json.loads(line)                         # 모든 로그 줄이 JSON


# ---------- B12/C8: 스키마 분리(PostgreSQL) ----------

def test_verify_mode_needs_no_ddl_privilege(request, tmp_path):
    from conftest import _pg_schema_url
    import psycopg
    url = _pg_schema_url(request)
    base = os.environ["TEST_DATABASE_URL"]
    monkey_env = dict(os.environ)
    os.environ["OPS_DB_SCHEMA"] = "verify"
    try:
        with pytest.raises(BoundaryError) as e:                           # 표가 없으면 시작을 거부하고 migrate를 안내
            LogStore.postgres(url)
        assert e.value.code == "FAILED_PRECONDITION" and "migrate" in e.value.message
        os.environ["OPS_DB_SCHEMA"] = "apply"
        LogStore.postgres(url)._backend.close()                           # migrate 단계(소유자 계정)
        FailureStoreImpl.postgres(url)._b.close()
        schema = url.split("search_path%3D")[1]
        role = "ops_app_" + schema[-8:]
        with psycopg.connect(base, autocommit=True) as c:
            c.execute(f"CREATE ROLE {role} LOGIN PASSWORD 'pw-app-1'")
            try:
                c.execute(f"GRANT USAGE ON SCHEMA {schema} TO {role}")
                c.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {schema} TO {role}")
                c.execute(f"GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA {schema} TO {role}")
                u = psycopg.conninfo.conninfo_to_dict(base)
                app_url = f"postgresql://{role}:pw-app-1@{u['host']}:{u['port']}/{u['dbname']}?options=-csearch_path%3D{schema}"
                os.environ["OPS_DB_SCHEMA"] = "verify"
                app = LogStore.postgres(app_url)                           # DDL 권한 없는 계정으로 시작한다
                assert app.send(envelope(make_trace("s-v", "t-v")))["status"] == "ACCEPTED"
                with pytest.raises(psycopg.Error):                         # 이 계정은 실제로 DDL을 못 한다
                    app._backend._db.execute("CREATE TABLE should_fail(x int)")
                app._backend.close()
            finally:
                c.execute(f"DROP OWNED BY {role}")
                c.execute(f"DROP ROLE {role}")
    finally:
        os.environ.clear()
        os.environ.update(monkey_env)


# ---------- round2 M2: 제곱 시간 정규식(ReDoS) ----------

def _timed(fn, arg):
    t = time.perf_counter()
    try:
        fn(arg)
    except BoundaryError:
        pass                    # 거부는 정상 결과다. 시간만 본다
    return time.perf_counter() - t


HOSTILE = {
    "no_at": lambda n: "a" * n,
    "all_at": lambda n: "@" * n,
    "dots": lambda n: "." * n,
    "at_then_labels": lambda n: "a@" + "b." * (n // 2),
    "digits": lambda n: "1" * n,
    "zero_digits": lambda n: "0" * n,
    "zero_space": lambda n: "0 " * (n // 2),
    "local_chars": lambda n: "a." * (n // 2),
    "hangul": lambda n: "가" * n,
}


def test_review_pii_scan_is_linear_for_hostile_inputs():
    from ops_feedback.review import MAX_FIELD_CHARS, ReviewQueue
    q = ReviewQueue(FailureStoreImpl.memory())
    for name, mk in HOSTILE.items():
        small, big = mk(MAX_FIELD_CHARS // 4)[:MAX_FIELD_CHARS // 4], mk(MAX_FIELD_CHARS)[:MAX_FIELD_CHARS]
        t_small = max(_timed(lambda v: q._reject_pii(v, "expected"), small), 5e-3)      # 잡음 하한: 아주 짧은 시간끼리의 비율은 의미가 없다
        t_big = _timed(lambda v: q._reject_pii(v, "expected"), big)
        assert t_big < 0.5, (name, t_big)
        assert t_big / t_small < 12, (name, t_small, t_big)             # 길이 4배에 제곱이면 16배 이상


def test_review_field_length_cap_and_nesting():
    from ops_feedback.review import MAX_FIELD_CHARS, ReviewQueue
    q = ReviewQueue(FailureStoreImpl.memory())
    q._reject_pii("a" * MAX_FIELD_CHARS, "expected")
    with pytest.raises(BoundaryError) as e:
        q._reject_pii("a" * (MAX_FIELD_CHARS + 1), "expected")
    assert e.value.code == "INVALID_RECORD"
    deep = cur = []
    for _ in range(200):
        nxt = []
        cur.append(nxt)
        cur = nxt
    with pytest.raises(BoundaryError):
        q._reject_pii(deep, "expected")


def test_review_email_detector_matches_the_old_regex_on_normal_input():
    import re
    from ops_feedback.review import _has_email
    old = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    for t in ["a@b.co", "a@b.c", "@b.co", "x a@b.com y", "a@.co", "연락 hong.gd+x@mail.example.org 주세요", "no at sign", "a@b", "a@b.c0", "john@x.io."]:
        assert bool(old.search(t)) == _has_email(t), t


def test_review_http_rejects_huge_and_slow_bodies_quickly(pipeline):
    pipeline["log"].send(envelope(pii_trace("s-1", "t-1"), release_id="rel-0002"))
    run(pipeline)
    (c,), _ = pipeline["store"].list_cases()
    s = make_server(LogStore.memory(), "ing", "127.0.0.1", 0, failures=pipeline["store"], read_tokens=parse_read_tokens("r=rt"),
                    review_tokens=parse_read_tokens("reviewer=vt"), require_reviewer_identity=False)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    port = s.server_address[1]

    def post(body):
        cn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        t = time.perf_counter()
        try:
            cn.request("POST", f"/v1/cases/{c['case_id']}/review", body=body, headers={"Authorization": "Bearer vt", "Content-Type": "application/json"})
            return cn.getresponse().status, time.perf_counter() - t
        except (BrokenPipeError, ConnectionResetError):          # 서버가 본문을 읽기 전에 거부하고 닫았다(큰 본문): 빠른 거부로 본다
            return 400, time.perf_counter() - t
    try:
        big = json.dumps({"review_status": "CONFIRMED", "expected_kind": "LORA_B", "expected": "a" * (8 * 1024 * 1024 - 100)}).encode()
        st, dt = post(big)
        assert st == 400 and dt < 3                                         # 8 MiB: 본문 상한(1 MiB)에서 바로 거부
        mid = json.dumps({"review_status": "CONFIRMED", "expected_kind": "LORA_B", "expected": "a" * 900_000}).encode()
        st, dt = post(mid)
        assert st == 400 and dt < 3                                         # 필드 20,000자 상한
        ok = json.dumps({"review_status": "CONFIRMED", "expected_kind": "LORA_B", "expected": "혈압약은 지금도 드시나요?"}).encode()
        assert post(ok)[0] == 201
    finally:
        s.shutdown()


def test_all_other_regexes_are_linear_on_the_same_inputs():
    """비식별 기본 패턴·예시 정책의 잔존 패턴·전화·주민번호를 같은 입력 군으로 시험한다(20,000자와 80,000자)."""
    import re
    from ops_feedback.deid import DEFAULT_KEEP_PATTERNS
    cfg = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "config", "job.example.yaml")))
    pats = {f"keep:{k}": re.compile(v) for k, v in DEFAULT_KEEP_PATTERNS.items()}
    pats.update({f"residual:{k}": re.compile(v) for k, v in cfg["deid"]["residual_patterns"].items()})
    for name, rx in pats.items():
        is_keep = name.startswith("keep:")
        for hn, mk in HOSTILE.items():
            for n in (20_000, 80_000):
                s_ = mk(n)
                t = time.perf_counter()
                rx.fullmatch(s_) if is_keep else rx.search(s_)
                dt = time.perf_counter() - t
                assert dt < 0.5, (name, hn, n, dt)


def test_deid_scans_long_strings_in_windows_instead_of_rejecting_them():
    """문진 스키마에는 maxLength가 없어 정상 trace에도 긴 문자열이 남을 수 있다(scrub 키). 거부하지 않고 창으로 검사하며, 긴 문자열 속 개인정보는 잡는다."""
    from ops_feedback.deid import DeidError
    cfg = base_config()
    cfg["deid"]["keep_string_patterns"] = {"next_item": r".*"}                 # 값 검증을 느슨하게 둔 정책
    long_ok = "가" * 100_000
    t0 = time.perf_counter()
    r = _deid(cfg).deidentify(envelope(_trace_with(next_item=long_ok)))        # 정상 trace는 닫히지 않는다
    assert r.trace is not None
    for pos in (0, 4000, 50_000, 99_000):                                     # 창 경계 근처를 포함해 어디에 있어도 잡는다
        s_ = long_ok[:pos] + "a@example.com" + long_ok[pos:]
        with pytest.raises(DeidError):
            _deid(cfg).deidentify(envelope(_trace_with(next_item=s_)))
    assert time.perf_counter() - t0 < 5


def test_ingest_of_a_huge_free_text_field_is_fast(log_store):
    t = make_trace("s-big", "t-big")
    t["frontend_message"] = {**t.get("frontend_message", {}), "utterance": "a" * (7 * 1024 * 1024)} if isinstance(t.get("frontend_message"), dict) else t.get("frontend_message")
    t0 = time.perf_counter()
    try:
        log_store.send(envelope(t))
    except BoundaryError:
        pass
    assert time.perf_counter() - t0 < 10


# ---------- round2 M3: 보존 불리언 엄격 ----------

@pytest.mark.parametrize("key", ["cases_purge_confirmed", "purge_unprocessed"])
@pytest.mark.parametrize("bad", ["false", "False", "no", "0", 0, 1, None, "true"])
def test_retention_boolean_keys_reject_non_booleans(tmp_path, key, bad):
    with pytest.raises(ValueError):
        _write(tmp_path, lambda c: c["retention"].update({key: bad}))


def test_retention_boolean_keys_accept_real_booleans(tmp_path):
    cfg = _write(tmp_path, lambda c: c["retention"].update({"cases_purge_confirmed": False, "purge_unprocessed": True}))
    assert cfg["retention"]["cases_purge_confirmed"] is False


def test_rule_enabled_must_be_boolean():
    from ops_feedback.detect import build_detectors
    with pytest.raises(ValueError):
        build_detectors([{"type": "rule", "rules": [{"id": "COMMIT_ERROR", "enabled": "false"}]}])


def test_job_default_deletes_confirmed_but_admin_purge_keeps_them_without_flag(tmp_path):
    from test_conformance_failurestore import TRACE, case
    ldb, fdb = str(tmp_path / "l.db"), str(tmp_path / "f.db")
    LogStore.sqlite(ldb)
    fail = FailureStoreImpl.sqlite(fdb)
    fail.put_trace("tr-1", TRACE)
    fail.publish_case(case("fc-c", created_at="2026-01-01T00:00:00Z"))
    fail.publish_case(case("fc-c", version=2, review_status="CONFIRMED", expected={"items": []}, created_at="2026-10-01T00:00:00Z"))
    base = [sys.executable, "-m", "ops_feedback.admin", "--log-db", ldb, "--failure-db", fdb]
    r = subprocess.run(base + ["purge", "--cases-days", "30"], capture_output=True, text=True, env=os.environ)
    assert r.returncode == 0 and json.loads(r.stdout)["cases"]["cases"] == 0               # 확정 사례는 플래그 없이는 보존
    r = subprocess.run(base + ["purge", "--cases-days", "30", "--include-confirmed"], capture_output=True, text=True, env=os.environ)
    assert json.loads(r.stdout)["cases"]["cases"] == 2
    # 잡의 기본(cases_purge_confirmed 생략)은 확정도 지운다: 두 기본값의 차이는 docs/boundary.md에 적혀 있다


# ---------- L: 재시도 대상 원본, delete-session 0건, 겹침, readyz 경고 ----------

def test_failed_rows_are_not_removed_by_regular_retention_but_by_their_own_deadline(pipeline):
    log = pipeline["log"]
    log._clock = lambda: "2026-01-01T00:00:00Z"
    for t in ("a", "b", "c"):
        log.send(envelope(make_trace("s", t), release_id="rel-0002"))
    log.mark("s", "a", "DEID_FAILED", "h", "2026-01-01T00:00:00Z")
    log.mark("s", "b", "PUBLISH_FAILED", "h", "2026-01-01T00:00:00Z")
    log.mark("s", "c", "DONE_CASE", "h", "2026-01-01T00:00:00Z")
    assert log.purge_received_before("2026-06-01T00:00:00Z", processed_only=True) == 1                 # DONE_CASE만
    assert log.purge_failed_before("2026-06-01T00:00:00Z") == 2
    assert log.counts_by_release() == {}


def test_job_uses_separate_failed_deadline(pipeline):
    log = pipeline["log"]
    log._clock = lambda: "2026-06-20T00:00:00Z"
    log.send(envelope(make_trace("s", "a"), release_id="rel-0002"))
    log.mark("s", "a", "DEID_FAILED", "h", NOW)
    pipeline["cfg"]["retention"] = {"raw_days": 30, "failed_raw_days": 90, "cases_days": "forever", "metrics_days": "forever"}
    assert run(pipeline, now="2026-08-20T00:00:00Z").purged_failed_raw == 0         # 61일: 일반 기한(30일)은 지났지만 별도 기한(90일) 전
    assert run(pipeline, now="2026-10-14T00:00:00Z").purged_failed_raw == 1


def test_delete_session_reports_nothing_found_with_exit_3(tmp_path):
    ldb, fdb = str(tmp_path / "l.db"), str(tmp_path / "f.db")
    LogStore.sqlite(ldb)
    FailureStoreImpl.sqlite(fdb)
    env = {**os.environ, "OPS_DEID_SALT": "test-salt-0123456789abcdef-0123456789abcdef"}
    r = subprocess.run([sys.executable, "-m", "ops_feedback.admin", "--log-db", ldb, "--failure-db", fdb, "delete-session", "--session-id", "none"],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 3 and "NOTHING_FOUND" in r.stdout


def test_delete_session_finds_raw_session_id_when_policy_does_not_pseudonymize(tmp_path):
    ldb, fdb = str(tmp_path / "l.db"), str(tmp_path / "f.db")
    log, fail = LogStore.sqlite(ldb), FailureStoreImpl.sqlite(fdb)
    t = make_trace("s-raw", "t-1")
    fail.put_trace("tr-x", {"session_id": "s-raw", "turn_id": "p-1"})
    from test_conformance_failurestore import case
    fail.publish_case(case("fc-raw", deidentified_trace_ref="tr-x"))
    log.send(envelope(t))
    r = subprocess.run([sys.executable, "-m", "ops_feedback.admin", "--log-db", ldb, "--failure-db", fdb, "delete-session", "--session-id", "s-raw"],
                       env={**os.environ, "OPS_DEID_SALT": "test-salt-0123456789abcdef-0123456789abcdef"}, capture_output=True, text=True)
    assert r.returncode == 0 and json.loads(r.stdout)["cases"] == 1 and json.loads(r.stdout)["raw_rows"] == 1


def test_job_retries_publish_once_when_trace_was_purged_in_between(pipeline):
    pipeline["log"].send(envelope(pii_trace("s-1", "t-1"), release_id="rel-0002"))
    store = pipeline["store"]
    orig_put = store.put_trace
    orig_pub = store.publish_case
    calls = {"n": 0}

    def put(ref, tr):
        orig_put(ref, tr)
        if calls["n"] == 0:                       # 저장 직후 admin purge가 고아 trace를 지웠다고 가정
            calls["n"] += 1
            store._b.delete_traces([ref])
    store.put_trace = put
    rep = run(pipeline)
    store.put_trace, store.publish_case = orig_put, orig_pub
    assert rep.cases_published == 1 and not rep.errors


def test_readyz_warns_when_reviewer_identity_not_required():
    s, port = _server(require_reviewer_identity=False)
    try:
        body = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz").read())
        assert "REVIEW_IDENTITY_NOT_REQUIRED" in body["warnings"]
    finally:
        s.shutdown()


# ---------- round3 M1: failed_raw_days 기본값 없음, raw_days를 따른다 ----------

@pytest.mark.parametrize("raw_days", [365, "forever", None])            # None = 적지 않음(기본 30)
@pytest.mark.parametrize("failed", ["absent", 7, "forever"])
def test_failed_raw_retention_follows_raw_days_unless_explicit(pipeline, raw_days, failed):
    log = pipeline["log"]
    log._clock = lambda: "2020-01-01T00:00:00Z"
    log.send(envelope(make_trace("s", "a"), release_id="rel-0002"))
    log.mark("s", "a", "DEID_FAILED", "h", "2020-01-01T00:00:00Z")
    ret = {"cases_days": "forever", "metrics_days": "forever"}
    if raw_days is not None:
        ret["raw_days"] = raw_days
    if failed != "absent":
        ret["failed_raw_days"] = failed
    pipeline["cfg"]["retention"] = ret
    # 2020-01-01 수신, 200일 뒤: raw_days 365/forever면 남고, 기본 30이면 지워진다. 명시 7이면 지워지고 명시 forever면 남는다
    purged = run(pipeline, now="2020-07-20T00:00:00Z").purged_failed_raw
    if failed == 7:
        expect = 1
    elif failed == "forever":
        expect = 0
    else:
        expect = 0 if raw_days in (365, "forever") else 1
    assert purged == expect, (raw_days, failed)


def test_load_config_does_not_fill_failed_raw_days(tmp_path):
    assert "failed_raw_days" not in _write(tmp_path, lambda c: None)["retention"]
    with pytest.raises(ValueError):
        _write(tmp_path, lambda c: c["retention"].update({"failed_raw_days": None}))
    with pytest.raises(ValueError):
        _write(tmp_path, lambda c: c["retention"].update({"failed_raw_days": 0}))


# ---------- round3 L3: dry-run이 원본만 있는 세션도 센다 ----------

def test_delete_session_dry_run_counts_raw_rows(tmp_path):
    ldb, fdb = str(tmp_path / "l.db"), str(tmp_path / "f.db")
    log = LogStore.sqlite(ldb)
    FailureStoreImpl.sqlite(fdb)
    log.send(envelope(make_trace("s-only-raw", "t-1")))
    base = [sys.executable, "-m", "ops_feedback.admin", "--log-db", ldb, "--failure-db", fdb, "delete-session", "--session-id", "s-only-raw"]
    env = {**os.environ, "OPS_DEID_SALT": "test-salt-0123456789abcdef-0123456789abcdef"}
    dry = subprocess.run(base + ["--dry-run"], env=env, capture_output=True, text=True)
    assert dry.returncode == 0 and json.loads(dry.stdout)["raw_rows"] == 1
    assert log.count_session("s-only-raw") == 1                                # dry-run은 지우지 않는다
    real = subprocess.run(base, env=env, capture_output=True, text=True)
    assert real.returncode == 0 and json.loads(real.stdout)["raw_rows"] == 1
    gone = subprocess.run(base + ["--dry-run"], env=env, capture_output=True, text=True)
    assert gone.returncode == 3

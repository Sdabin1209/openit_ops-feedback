"""boundary_contracts.clients(v1.2.0)와의 통합: 우리가 소비자일 때(I4)와 서버일 때(I6·I7)."""
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from boundary_contracts import PIPELINE_SCHEMA_VERSION
from boundary_contracts import content_hash, sha256_hash
from boundary_contracts.clients import HttpFailureStore, HttpReleaseReader, HttpTraceIngest
from boundary_contracts.errors import BoundaryError
from boundary_contracts.sdk import FailureStore, ReleaseReader, TraceIngest

from conftest import envelope, make_trace, pii_trace
from ops_feedback.api import make_server, parse_read_tokens
from ops_feedback.config import load_config
from ops_feedback.job import build_from_config, run_once
from ops_feedback.failurestore import FailureStoreImpl
from ops_feedback.logstore import LogStore

QS = {"id": "qs-main", "version": "3", "content_hash": "sha256:" + "1" * 64}


def _manifest(rid):
    m = {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "release_id": rid, "created_at": "2026-10-14T00:00:00Z", "gate_decision_id": "gd-1",
         "question_set": QS,
         "lora_a": {"id": "lora-a", "version": "1", "content_hash": "sha256:" + "2" * 64},
         "lora_b": {"id": "lora-b", "version": "1", "content_hash": "sha256:" + "3" * 64},
         "runtime_contract_version": 1, "turn_engine_version": "0.2.0", "model_runtime_version": "0.4.0",
         "adapters": {r: {"adapter_hash": "sha256:" + "4" * 64, "serving_config": {"k": 1},
                          "serving_config_hash": sha256_hash({"k": 1}),
                          "system_prompt": {"path": "system_prompt.txt", "sha256": "sha256:" + "5" * 64}} for r in ("LORA_A", "LORA_B")}}
    m["content_hash"] = content_hash(m)
    return m


class MiniReleaseServer:
    """HTTP.md I4의 작은 서버(참조 서버와 같은 방식). 읽기 토큰만 받는다."""

    def __init__(self, token="gate-read"):
        self.token, self.seen = token, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def do_GET(self):
                outer.seen.append(self.headers.get("Authorization"))
                if self.headers.get("Authorization") != "Bearer " + outer.token:
                    return self._send(403, {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "code": "PERMISSION_DENIED", "message": "no", "retryable": False})
                if self.path == "/v1/active-pointer":
                    return self._send(200, {"item": {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "release_id": "rel-0002", "pointer_version": 1,
                                                     "activated_at": "2026-10-15T00:00:00Z", "cause": "ACTIVATE"}})
                if self.path.startswith("/v1/releases/"):
                    rid = self.path.rsplit("/", 1)[1]
                    if rid == "rel-missing":
                        return self._send(404, {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "code": "NOT_FOUND", "message": "x", "retryable": False})
                    return self._send(200, _manifest(rid))
                self._send(404, {"pipeline_schema_version": PIPELINE_SCHEMA_VERSION, "code": "NOT_FOUND", "message": "x", "retryable": False})

            def _send(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def stop(self):
        self.srv.shutdown()


@pytest.fixture
def gate():
    s = MiniReleaseServer()
    yield s
    s.stop()


def test_release_reader_factory_from_config(tmp_path, gate, monkeypatch, cfg):
    import yaml
    cfg["storage"] = {"log_db": str(tmp_path / "l.db"), "failure_db": str(tmp_path / "f.db")}
    cfg["release_reader"] = {"factory": "boundary_contracts.clients:HttpReleaseReader",
                             "args": {"base_url": gate.url, "token": {"env": "GATE_READ"}}}
    p = tmp_path / "job.yaml"
    p.write_text(yaml.safe_dump(cfg))
    env = {"OPS_DEID_SALT": "s", "GATE_READ": "gate-read"}
    log, store, deid, detectors, classifier, planner, reader = build_from_config(load_config(p), env)
    assert isinstance(reader, HttpReleaseReader) and isinstance(reader, ReleaseReader)
    assert reader.get_release("rel-0002")["question_set"] == QS     # 클라이언트 자체가 읽을 수 있어야 한다
    log.send(envelope(pii_trace("s-1", "t-1"), release_id="rel-0002"))
    rep = run_once(cfg=load_config(p), log=log, store=store, deid=deid, detectors=detectors, classifier=classifier,
                   clock=lambda: "2026-10-15T00:00:00Z", release_reader=reader)
    assert rep.cases_published == 1
    (c,), _ = store.list_cases()
    assert c["question_set"] == QS                                  # 실제 클라이언트로 읽은 ReleaseManifest에서
    assert "Bearer gate-read" in gate.seen


def test_unreadable_release_omits_question_set(gate, pipeline):
    bad = HttpReleaseReader(gate.url, "wrong-token")
    pipeline["log"].send(envelope(pii_trace(), release_id="rel-0002"))
    rep = run_once(cfg=pipeline["cfg"], log=pipeline["log"], store=pipeline["store"], deid=pipeline["deid"],
                   detectors=pipeline["detectors"], classifier=pipeline["classifier"], clock=pipeline["clock"], release_reader=bad)
    (c,), _ = pipeline["store"].list_cases()
    assert rep.cases_published == 1 and "question_set" not in c


def test_missing_env_for_factory_arg(tmp_path, gate, cfg):
    import yaml
    cfg["storage"] = {"log_db": str(tmp_path / "l.db"), "failure_db": str(tmp_path / "f.db")}
    cfg["release_reader"] = {"factory": "boundary_contracts.clients:HttpReleaseReader",
                             "args": {"base_url": gate.url, "token": {"env": "NOPE"}}}
    p = tmp_path / "job.yaml"
    p.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError):
        build_from_config(load_config(p), {"OPS_DEID_SALT": "s"})


# ---- 우리가 서버 ----
@pytest.fixture
def ours(failure_store, tmp_path):
    from test_conformance_failurestore import case
    failure_store.put_trace("tr-1", {"session_id": "p-x", "turn_id": "p-y"})
    for i in range(3):
        failure_store.publish_case(case(f"fc-{i}", failure_type="A" if i < 2 else "B"))
    failure_store.publish_case(case("fc-0", version=2, review_status="CONFIRMED", created_at="2026-10-16T00:00:00Z"))
    s = make_server(LogStore.memory(), "ingest-tok", "127.0.0.1", 0, failures=failure_store,
                    read_tokens=parse_read_tokens("synthetic-data=read-tok"))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


def test_ingest_client_accept_duplicate_and_read_token_forbidden(ours):
    c = HttpTraceIngest(ours, "ingest-tok")
    assert isinstance(c, TraceIngest)
    assert c.send(envelope())["status"] == "ACCEPTED"
    assert c.send(envelope())["status"] == "DUPLICATE"
    with pytest.raises(BoundaryError) as x:
        HttpTraceIngest(ours, "read-tok").send(envelope(make_trace("s-2", "t-2")))
    assert x.value.code == "PERMISSION_DENIED"


def test_failure_store_client_reads(ours):
    fs = HttpFailureStore(ours, "read-tok")
    assert isinstance(fs, FailureStore)
    assert fs.get_case("fc-0")["version"] == 2
    assert fs.get_case("fc-0", version=1)["review_status"] == "PENDING"
    with pytest.raises(BoundaryError) as x:
        fs.get_case("nope")
    assert x.value.code == "NOT_FOUND"
    items, nxt = fs.list_cases()
    assert {c["case_id"] for c in items} == {"fc-0", "fc-1", "fc-2"} and nxt is None
    assert len(fs.list_cases(latest_only=False)[0]) == 4
    assert [c["case_id"] for c in fs.list_cases(failure_type="B")[0]] == ["fc-2"]
    assert [c["case_id"] for c in fs.list_cases(review_status="CONFIRMED")[0]] == ["fc-0"]
    assert [c["case_id"] for c in fs.list_cases(source_release_id="rel-0001")[0]][:1] == ["fc-1"]
    assert [c["case_id"] for c in fs.list_cases(updated_since=datetime(2026, 10, 16, tzinfo=timezone.utc))[0]] == ["fc-0"]
    seen, cur = [], None
    while True:
        items, cur = fs.list_cases(limit=2, cursor=cur)
        seen += [c["case_id"] for c in items]
        if cur is None:
            break
    assert sorted(seen) == ["fc-0", "fc-1", "fc-2"]
    assert fs.get_trace("tr-1") == {"session_id": "p-x", "turn_id": "p-y"}
    with pytest.raises(BoundaryError) as x:
        fs.get_trace("zzz")
    assert x.value.code == "NOT_FOUND"


def test_failure_store_client_rejects_wrong_token_and_publish(ours):
    with pytest.raises(BoundaryError) as x:
        HttpFailureStore(ours, "ingest-tok").list_cases()
    assert x.value.code == "PERMISSION_DENIED"
    with pytest.raises(BoundaryError):
        HttpFailureStore(ours, "read-tok").publish_case({})

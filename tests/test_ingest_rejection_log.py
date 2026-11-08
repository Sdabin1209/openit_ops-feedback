"""대장 #20: 수집 거부(400 INVALID_RECORD 등)는 서버 로그에 사유(코드·메시지·어긋난 칸 이름)를 남기고, 발화·비밀·원문 ID 는 남기지 않는다."""
import json
import logging
import threading

import pytest

from conftest import envelope, make_trace
from test_http_401 import ING, call, make_server
from ops_feedback.wiring.log_store import LogStore

SECRET = "SECRET-UTT-7731"
SECRET_ID = "ORIGINAL-ID-9921_x"


@pytest.fixture
def port():
    s = make_server(LogStore.memory(), ING, "127.0.0.1", 0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s.server_address[1]
    s.shutdown()


def post(port, env_or_raw):
    raw = env_or_raw if isinstance(env_or_raw, bytes) else json.dumps(env_or_raw).encode()
    return call(port, "POST", "/v1/traces", f"Bearer {ING}", body=raw)[0]


def bad_envelope_field():
    e = envelope(make_trace("s-1", "t-1"))
    e["release_id"] = f"{SECRET} !"                       # Id 형식 위반(칸: release_id)
    return e


def bad_trace_field():
    e = envelope(make_trace("s-1", "t-1"))
    e["trace"]["turn_index"] = SECRET                     # TurnTrace 위반(정수 칸에 문자열)
    return e


def id_mismatch():
    e = envelope(make_trace(SECRET_ID, "t-1"))
    e["session_id"] = "s-other"
    return e


@pytest.mark.parametrize("make,expect", [
    (bad_envelope_field, ["INVALID_RECORD", "release_id"]),
    (bad_trace_field, ["INVALID_RECORD", "turn_index"]),
    (id_mismatch, ["INVALID_RECORD", "session_id/turn_id"]),
    (lambda: b"{not json " + SECRET.encode(), ["INVALID_RECORD", "JSON"]),
])
def test_rejected_ingest_logs_code_and_reason_without_values(port, caplog, make, expect):
    with caplog.at_level(logging.INFO, logger="ingest-api"):
        assert post(port, make()) == 400
    rej = [r.getMessage() for r in caplog.records if r.getMessage().startswith("거부 ")]
    assert len(rej) == 1, caplog.text
    assert all(x in rej[0] for x in expect), rej[0]
    assert SECRET not in caplog.text and SECRET_ID not in caplog.text


def test_accepted_ingest_logs_no_rejection(port, caplog):
    with caplog.at_level(logging.INFO, logger="ingest-api"):
        assert post(port, envelope(make_trace("s-1", "t-1"))) == 200
    assert not [r for r in caplog.records if r.getMessage().startswith("거부 ")]

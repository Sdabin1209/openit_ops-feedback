"""OF-03: 세션 삭제(admin)와 비식별 처리기(deid)가 의사 식별자 계산을 한 함수로 공유한다.
한쪽만 바뀌면 개인정보 삭제가 조용히 0건이 되던 결함의 재현 시험."""
import pytest

from conftest import base_config, envelope, make_trace, pii_trace
from ops_feedback.wiring import admin_flow as admin
from ops_feedback.components import pseudonymizer as pseudo_mod


def pseudonym_of(salt, key, value):
    return pseudo_mod.pseudonymize({"salt": salt, "key": key, "value": value})

from ops_feedback.components.deidentifier import DeidPolicy
from tdeid import Deidentifier
from ops_feedback.components.failure_detector import FailureDetector
from ops_feedback.components.failure_classifier import FailureClassifier as Classifier
from ops_feedback.components.failure_case_store import FailureCaseStore as FailureStoreImpl
from ops_feedback.wiring.pipeline import run_once
from ops_feedback.wiring.log_store import LogStore

NOW = "2026-10-14T12:00:00Z"
SALT = "of03-salt"


def _published(tmp_path):
    cfg = base_config()
    log, fail = LogStore.sqlite(tmp_path / "l.db"), FailureStoreImpl.sqlite(tmp_path / "f.db")
    d = Deidentifier(DeidPolicy.from_config(cfg["deid"]), SALT, log.grant_deidentifier_view())
    log.send(envelope(pii_trace("s-del", "t-1"), release_id="rel-0002"))
    run_once(cfg=cfg, log=log, store=fail, deid=d, pseudonymize=d._pseudonymize, detector=FailureDetector(cfg["detection"]["detectors"]), classifier=Classifier(cfg["classification"]), clock=lambda: NOW)
    assert len(fail.list_cases(latest_only=False)[0]) == 1
    return log, fail


def test_delete_session_removes_records_stored_under_deid_pseudonym(tmp_path):
    log, fail = _published(tmp_path)
    # 원본은 이미 처리돼 있으므로 원본 행만 지우는 경로가 아니라 비식별 trace·사례를 의사 식별자로 찾아 지우는지 본다
    out = admin.delete_session(log, fail, SALT, "s-del", dry_run=False)
    assert out["traces"] == 1 and out["cases"] == 1
    assert fail.list_cases(latest_only=False)[0] == []


def test_changing_deid_pseudonym_computation_does_not_break_delete(tmp_path, monkeypatch):
    """deid 쪽 계산을 바꿔도(monkeypatch) admin이 같은 함수를 쓰므로 삭제가 여전히 된다."""
    real = pseudo_mod.hmac
    # 의사 식별자 계산(HMAC 호출)만 다른 값으로 바꾼다
    class Patched:
        @staticmethod
        def new(key, msg, digest):
            return real.new(key, msg + b"v2", digest)
    monkeypatch.setattr(pseudo_mod, "hmac", Patched)
    log, fail = _published(tmp_path)
    out = admin.delete_session(log, fail, SALT, "s-del", dry_run=False)
    assert out["traces"] == 1 and out["cases"] == 1, "deid와 admin이 다른 식을 쓰면 여기서 0건이 된다"
    assert fail.list_cases(latest_only=False)[0] == []


def test_admin_pseudonym_equals_value_made_by_deid(tmp_path):
    """기대값은 deid 함수가 만든 값 자체다(admin 쪽에서 식을 복제해 계산하지 않는다)."""
    d = Deidentifier(DeidPolicy.from_config(base_config()["deid"]), SALT)
    assert admin.session_pseudonym(SALT, "s-del") == d.pseudonym("session_id", "s-del") == pseudonym_of(SALT, "session_id", "s-del")

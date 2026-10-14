import json

import pytest

from conftest import FAKE_PII, envelope, make_trace, pii_trace, base_config
from ops_feedback.deid import MASK, DROPPED, DeidError, Deidentifier, DeidPolicy


def deid(cfg=None):
    return Deidentifier(DeidPolicy.from_config((cfg or base_config())["deid"]), "salt-1")


def test_no_pii_survives_anywhere():
    r = deid().deidentify(envelope(pii_trace()))
    blob = json.dumps(r.trace, ensure_ascii=False)
    for v in FAKE_PII.values():
        assert v not in blob
    assert "120" not in blob                      # 환자 답변 값(Value가 아닌 Item.value 문자열도 마스킹)
    assert "혈압" not in blob                      # source_text


def test_structure_kept():
    t = deid().deidentify(envelope(pii_trace())).trace
    assert t["generator_output"]["is_fallback"] is True
    assert t["parser_output"]["items"][0]["intent"] == "ANSWERED_INTENT"
    assert t["parser_output"]["items"][0]["target"]["question_id"] == "q1"
    assert t["parser_output"]["items"][0]["evidence"][0]["text"] == MASK
    assert t["frontend_message"]["utterance"] == MASK
    assert t["frontend_message"]["kind"] == "UTTERANCE"
    assert t["contract_version"] == 1


def test_ids_pseudonymized_consistently():
    d = deid()
    t = d.deidentify(envelope(pii_trace("s-secret", "t-secret"))).trace
    assert t["session_id"] == t["frontend_message"]["session_id"] == d.pseudonym("session_id", "s-secret")
    assert "s-secret" not in json.dumps(t)
    other = Deidentifier(DeidPolicy.from_config(base_config()["deid"]), "salt-2")
    assert other.deidentify(envelope(pii_trace("s-secret", "t-secret"))).trace["session_id"] != t["session_id"]


def test_deterministic():
    a = deid().deidentify(envelope(pii_trace()))
    b = deid().deidentify(envelope(pii_trace()))
    assert a.trace == b.trace and a.stats == b.stats


def test_unknown_keys_and_free_objects_dropped():
    tr = pii_trace()
    tr["frontend_message"]["secret_extra"] = "홍길동"      # 스키마에 없는 키
    tr["judge_input"] = None
    r = deid().deidentify(envelope(tr))
    # 스키마에 없는 키가 있으면 가지가 어느 정의에도 맞지 않아 통째로 버려진다(보수적 제거)
    assert "frontend_message" not in r.trace
    assert r.stats["dropped_unmatched"] >= 1
    # 맞는 가지 안의 미지 키는 키만 버려진다
    tr2 = pii_trace()
    tr2["parser_output"]["items"][0]["extra"] = "홍길동"
    r2 = deid().deidentify(envelope(tr2))
    assert "parser_output" not in r2.trace or "홍길동" not in json.dumps(r2.trace, ensure_ascii=False)
    assert "홍길동" not in json.dumps(r.trace, ensure_ascii=False)


def test_drop_def_and_mask_def():
    tr = make_trace(record_writer_output={"store_decisions": [{"slot": {"question_id": "q1", "repeat_key": "", "slot_id": "a"},
                                                                  "action": "FIRST_WRITE", "value": {"amount": 120, "unit": "mmHg"},
                                                                  "pending_id": None, "risk_mark": None,
                                                                  "value_metadata": {"precision": "exact", "evidence": []}}],
                                          "store_summary": {"changed_slots": [], "unknown_refused_records": [],
                                                            "created_pendings": [], "resolved_pendings": [], "invalidated_questions": []}})
    t = deid().deidentify(envelope(tr)).trace
    assert t["record_writer_output"]["store_decisions"][0]["value"] == MASK
    assert t["record_writer_output"]["store_decisions"][0]["action"] == "FIRST_WRITE"


def test_residual_pattern_fails_closed():
    cfg = base_config()
    cfg["deid"]["keep_string_keys"].append("utterance")        # 사용자가 발화를 남기기로 했는데 전화번호가 있다
    with pytest.raises(DeidError):
        deid(cfg).deidentify(envelope(pii_trace()))


def test_scrub_keys_replace_patterns():
    cfg = base_config()
    cfg["deid"]["scrub_string_keys"] = ["utterance"]
    cfg["deid"]["scrub_patterns"] = {"PHONE": r"0\d{1,2}-\d{3,4}-\d{4}", "EMAIL": r"[\w.]+@[\w.]+\.\w+"}
    t = deid(cfg).deidentify(envelope(pii_trace())).trace
    assert "[PHONE]" in t["frontend_message"]["utterance"] and "010-1234-5678" not in t["frontend_message"]["utterance"]


def test_policy_has_no_defaults():
    with pytest.raises(ValueError):
        DeidPolicy.from_config({"policy_id": "x"})
    with pytest.raises(ValueError):
        DeidPolicy.from_config({"policy_id": "x", "keep_string_keys": [], "unlisted_string_action": "keep"})


def test_unlisted_drop_action():
    cfg = base_config()
    cfg["deid"]["unlisted_string_action"] = "drop"
    t = deid(cfg).deidentify(envelope(pii_trace())).trace
    assert "utterance" not in t["frontend_message"]

import json

import pytest

from conftest import FAKE_PII, envelope, make_trace, pii_trace, base_config
from ops_feedback.schemas import validate_trace
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
    assert t["response_outcome"] == "fallback"
    assert t["extract_status"] == "OK" and t["extract_attempts"] == 1 and t["validation_ok"] is True
    assert t["extract_output"]["action"] == "NEXT" and t["extract_output"]["next_item"] == "Q2"
    assert t["extract_input"]["question_id"] == "Q1"
    assert t["extract_output"]["values"][0]["key"] == MASK
    assert t["extract_output"]["values"][0]["save"] is True
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
    tr["judge_input"] = None                              # v1 키(없어진 키)
    # 스키마에 없는 키가 있으면 가지가 어느 정의에도 맞지 않아 통째로 버려진다(보수적 제거). 그러면 필수 키가 빠져
    # 비식별 뷰 계약(OpsFeedbackDeidentifiedTrace)을 어기므로 자기 검사가 이 턴을 닫는다(DEID_FAILED).
    with pytest.raises(DeidError, match="OpsFeedbackDeidentifiedTrace"):
        deid().deidentify(envelope(tr))
    # 맞는 가지 안의 미지 키는 키만 버려진다
    tr2 = pii_trace()
    tr2["extract_output"]["extra"] = "홍길동"
    tr2["extract_output"]["values"][0]["extra"] = "홍길동"
    r2 = deid().deidentify(envelope(tr2))
    assert "홍길동" not in json.dumps(r2.trace, ensure_ascii=False)
    assert r2.stats["dropped_unknown_keys"] >= 2


def test_mask_def_value_and_value_key():
    """저장된 값은 Value 정의로, 모델이 낸 추출 값(스키마가 형태를 정하지 않음)은 키로 가린다. 숫자·불리언도."""
    tr = make_trace(
        persistence_commit_request={
            "session_id": "s-1", "expected_revision": "r1",
            "writes": {"answer_updates": [{"slot": {"question_id": "Q1", "repeat_key": "", "slot_id": "sys"},
                                           "action": "FIRST_WRITE", "value": {"amount": 7731, "unit": "mmHg"}, "precision": "exact"}],
                       "progress_record_update": None}},
        extract_output={"values": [{"key": "Q1.sys", "value": 7731, "precision": "exact", "status": "ANSWERED", "save": True},
                                   {"key": "Q1.flag", "value": True, "precision": "exact", "status": "ANSWERED", "save": True},
                                   {"key": "Q1.obj", "value": {"amount": 4242, "unit": "kg"}, "precision": "exact", "status": "ANSWERED", "save": True},
                                   {"key": "Q1.none", "value": None, "precision": "exact", "status": "MISSING", "save": False}],
                        "action": "NEXT", "next_item": "Q2"})
    r = deid().deidentify(envelope(tr))
    vals = r.trace["extract_output"]["values"]
    assert [v["value"] for v in vals] == [MASK, MASK, MASK, MASK]       # null도 마스킹: 비식별 뷰 계약이 값을 [MASKED]|[DROPPED]만 허용
    assert "7731" not in json.dumps(r.trace) and "4242" not in json.dumps(r.trace)
    if "persistence_commit_request" in r.trace:
        assert r.trace["persistence_commit_request"]["writes"]["answer_updates"][0]["value"] == MASK
    assert r.stats["masked_by_key"] == 4


def test_policy_naming_a_missing_def_fails_closed():
    """문진 스키마가 바뀌어 정책이 지목한 정의가 사라지면 조용히 넘어가지 않는다(v2 TurnTrace에는 QuestionBank가 없다)."""
    cfg = base_config()
    cfg["deid"]["drop_defs"] = ["QuestionBank"]
    with pytest.raises(DeidError, match="QuestionBank"):
        deid(cfg).deidentify(envelope(make_trace()))


def test_residual_pattern_fails_closed():
    cfg = base_config()
    cfg["deid"]["keep_string_keys"].append("utterance")        # 사용자가 발화를 남기기로 했는데 전화번호가 있다
    with pytest.raises(DeidError):
        deid(cfg).deidentify(envelope(pii_trace()))


def test_scrub_keys_replace_patterns():
    cfg = base_config()
    cfg["deid"]["scrub_string_keys"] = ["utterance"]
    cfg["deid"]["scrub_patterns"] = {"PHONE": r"0\d{1,2}-\d{3,4}-\d{4}", "EMAIL": r"[\w.]+@[\w.]+\.\w+"}
    # 치환 문자열([PHONE])은 비식별 뷰 계약(자유 문자열은 [MASKED]|[DROPPED]만)에 없다: scrub 정책은 지금 자기 검사에서 닫힌다. 계약 변경 요청 CR-008.
    with pytest.raises(DeidError, match="OpsFeedbackDeidentifiedTrace"):
        deid(cfg).deidentify(envelope(pii_trace()))


def test_policy_has_no_defaults():
    with pytest.raises(ValueError):
        DeidPolicy.from_config({"policy_id": "x"})
    with pytest.raises(ValueError):
        DeidPolicy.from_config({"policy_id": "x", "keep_string_keys": [], "unlisted_string_action": "keep"})


def test_unlisted_drop_action():
    cfg = base_config()
    cfg["deid"]["unlisted_string_action"] = "drop"
    # drop은 키를 없애는데 계약은 필수 문자열 칸을 요구한다: 자기 검사에서 닫힌다(CR-008).
    with pytest.raises(DeidError, match="OpsFeedbackDeidentifiedTrace"):
        deid(cfg).deidentify(envelope(pii_trace()))


def test_pseudonyms_not_flagged_as_residual_pii():
    """의사 식별자(16진)의 숫자열이 우연히 전화번호 패턴에 걸려도 비식별이 실패하지 않는다."""
    import re
    d = deid()
    rx = re.compile(base_config()["deid"]["residual_patterns"]["phone_kr"])
    sid = next(f"s-{i}" for i in range(200000) if rx.search(d.pseudonym("session_id", f"s-{i}")))
    r = d.deidentify(envelope(make_trace(sid, "t-1")))
    assert rx.search(r.trace["session_id"])      # 패턴에는 걸리지만


# --- v2 TurnTrace의 발화 문장이 든 키를 하나씩 확인한다 -------------------------------------------------

# 새 키의 문자열(또는 형태 없는) 잎 -> 처분. 문진 스키마가 바뀌어 잎이 늘거나 줄면 test_every_new_key_leaf_is_classified가 실패해 검토를 강제한다.
KEPT, MASKED, DROPPED_ = "kept", "masked", "dropped"
DISPOSITION = {
    "extract_input.question_id": KEPT,
    "extract_input.current_question": MASKED,          # 직전 응답 문장(환자에게 한 말, 이름이 섞일 수 있다)
    "extract_input.user_utterance": MASKED,            # 환자 발화
    "extract_input.state": DROPPED_,                   # 저장된 값(건강 정보). 자유 객체라 통째로 비운다
    "extract_output.values[].key": MASKED,
    "extract_output.values[].value": MASKED,           # mask_value_keys
    "extract_output.values[].precision": MASKED,
    "extract_output.values[].status": MASKED,
    "extract_output.action": KEPT,                     # 규칙이 읽는다
    "extract_output.next_item": KEPT,                  # 규칙이 읽는다
    "extract_output.reason": MASKED,
    "extract_output.targets[]": MASKED,
    "extract_output.note": MASKED,
    "extract_protections": DROPPED_,                   # 자유 객체(키 이름도 값일 수 있어 비운다)
    "validation_violations[].code": KEPT,
    "validation_violations[].path": KEPT,
    "validation_violations[].message": MASKED,
    "response_input.action": KEPT,
    "response_input.topic": MASKED,
    "response_input.next": DROPPED_,                   # 다음 문항 문장(자유 객체)
    "response_output": MASKED,                         # 환자에게 한 응답 문장
    "response_protections": DROPPED_,
}
KEPT_VALUES = {"extract_input.question_id": "Q1", "extract_output.action": "REASK", "extract_output.next_item": "Q1",
               "validation_violations[].code": "UNKNOWN_SLOT", "validation_violations[].path": "values[0].key",
               "response_input.action": "REASK"}
NEW_KEYS = ["extract_input", "extract_output", "extract_protections", "validation_violations",
            "response_input", "response_output", "response_protections"]


def _leaves(defs: dict, node: dict, path: str, out: set, seen=()) -> None:
    while "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        if name in seen:
            return
        seen += (name,)
        node = defs[name]
    for k in ("oneOf", "anyOf"):
        if k in node:
            for b in node[k]:
                _leaves(defs, b, path, out, seen)
            return
    if "const" in node or "enum" in node:
        return
    t = node.get("type")
    ts = t if isinstance(t, list) else [t]
    if "string" in ts:
        out.add(path)
    if "properties" in node:
        for k, v in node["properties"].items():
            _leaves(defs, v, f"{path}.{k}", out, seen)
    elif "object" in ts or t is None and "items" not in node:
        if "string" not in ts and "boolean" not in ts and "integer" not in ts and "number" not in ts:
            out.add(path)                               # 형태가 없는 객체(자유 객체) 또는 아무 형태나 허용하는 잎
    if "items" in node:
        _leaves(defs, node["items"], path + "[]", out, seen)


def test_every_new_key_leaf_is_classified():
    import json as _j
    from ops_feedback.schemas import trace_schema
    defs = trace_schema(1)["$defs"]
    props = defs["TurnTrace"]["properties"]
    found: set = set()
    for k in NEW_KEYS:
        _leaves(defs, props[k], k, found)
    assert found == set(DISPOSITION), f"새 키의 잎이 바뀌었다. 검토 필요: 추가 {sorted(found - set(DISPOSITION))}, 삭제 {sorted(set(DISPOSITION) - found)}"


def _build_leaf_trace() -> dict:
    s = lambda p: KEPT_VALUES.get(p, f"SENT<{p}>홍길동")
    return make_trace(
        extract_input={"question_id": s("extract_input.question_id"), "current_question": s("extract_input.current_question"),
                       "user_utterance": s("extract_input.user_utterance"),
                       "state": {"Q1": {"SENT<state.key>": {"value": "SENT<state.value>", "status": "ANSWERED"}}, "n": 31337}},
        extract_output={"values": [{"key": s("extract_output.values[].key"), "value": s("extract_output.values[].value"),
                                    "precision": s("extract_output.values[].precision"), "status": s("extract_output.values[].status"), "save": True}],
                        "action": s("extract_output.action"), "next_item": s("extract_output.next_item"),
                        "reason": s("extract_output.reason"), "targets": [s("extract_output.targets[]")], "note": s("extract_output.note")},
        extract_attempts=2, extract_status="VIOLATION", extract_protections={"SENT<prot-key>": 31337},
        validation_ok=False,
        validation_violations=[{"code": s("validation_violations[].code"), "path": s("validation_violations[].path"),
                                "message": s("validation_violations[].message")}],
        response_input={"action": s("response_input.action"), "topic": s("response_input.topic"),
                        "next": {"id": "Q2", "text": "SENT<next.text>홍길동"}},
        response_output=s("response_output"), response_outcome="ok", response_protections={"SENT<prot2-key>": 7},
    )


def _get(d, path):
    cur = [d]
    for part in path.split("."):
        name, is_list = (part[:-2], True) if part.endswith("[]") else (part, False)
        nxt = []
        for c in cur:
            if isinstance(c, dict) and name in c:
                v = c[name]
                nxt.extend(v if is_list else [v])
        cur = nxt
    return cur


@pytest.mark.parametrize("path,disp", sorted(DISPOSITION.items()))
def test_new_key_leaf_disposition(path, disp):
    r = deid().deidentify(envelope(_build_leaf_trace())).trace
    got = _get(r, path)
    if disp == DROPPED_:
        assert got in ([], [{}]) or all(g in ({}, []) for g in got), f"{path}가 비워지지 않았다: {got}"
    elif disp == MASKED:
        assert got and all(g == MASK for g in got), f"{path}가 마스킹되지 않았다: {got}"
    else:
        assert got == [KEPT_VALUES[path]]


def test_fixture_traces_are_valid_v2_traces():
    validate_trace(1, pii_trace())
    validate_trace(1, _build_leaf_trace())


def test_no_sentinel_or_number_survives_in_new_keys():
    r = deid().deidentify(envelope(_build_leaf_trace()))
    blob = json.dumps(r.trace, ensure_ascii=False)
    assert "SENT<" not in blob and "홍길동" not in blob and "31337" not in blob
    # 통계의 경로도 값이 없다
    assert "SENT" not in json.dumps(r.stats, ensure_ascii=False)
    assert "extract_input.state" in r.stats["dropped_paths"] and "response_input.next" in r.stats["dropped_paths"]


def test_dialogue_log_in_session_documents_masked():
    """세션 문서 안 대화 로그(이전 턴의 발화·응답·추출 출력)도 같은 정책을 받는다."""
    entry = {"input": {"kind": "UTTERANCE", "utterance": "SENT<홍길동 010-0000-0000>", "intervention_decision": None},
             "handling": {"handled": "UTTERANCE"}, "question_bank_version": 1, "turn_result": "COMMITTED",
             "extract_output": {"values": [{"key": "Q1.a", "value": 55555, "precision": "exact", "status": "ANSWERED", "save": True}],
                                "action": "REASK", "next_item": "Q1", "note": "SENT<note>"},
             "validation_violations": [{"code": "X", "path": "p", "message": "SENT<msg>"}],
             "staff_alert": None, "response_text": "SENT<resp>", "staff_alert_delivery": "NOT_APPLICABLE",
             "turn_failure_signal": None, "intervention_result": None}
    sess = {"session_info": {"session_id": "s-1", "question_bank_version": 1},
            "answer_records": [{"slot": {"question_id": "Q1", "repeat_key": "", "slot_id": "a"},
                                "current_value": {"answer": 55555}, "precision": "exact", "risk_mark": None,
                                "history": [44444, {"value": True, "precision": "exact"}]}],
            "progress_record": {"current_question_id": "Q1", "open_slots": [{"key": "SENT<k>", "status": "MISSING"}],
                                "skipped": [], "session_status": "IN_PROGRESS", "pause_history": []},
            "dialogue_log": [entry], "dispatch_log": []}
    tr = make_trace(log_entry=entry, session_before=sess, session_after=sess)
    validate_trace(1, tr)                                  # 시험 자료가 문진 스키마를 지키는지
    r = deid().deidentify(envelope(tr))
    blob = json.dumps(r.trace, ensure_ascii=False)
    assert "SENT" not in blob and "55555" not in blob and "44444" not in blob and "010-0000" not in blob
    for k in ("session_before", "session_after"):
        log = r.trace[k]["dialogue_log"][0]
        assert log["input"]["utterance"] == MASK and log["response_text"] == MASK
        assert log["extract_output"]["action"] == "REASK" and log["extract_output"]["values"][0]["value"] == MASK
        assert r.trace[k]["answer_records"][0]["current_value"] == {"answer": MASK}
        assert r.trace[k]["answer_records"][0]["history"] == [MASK, {"value": MASK, "precision": "exact"}]
        assert r.trace[k]["progress_record"]["open_slots"][0]["key"] == MASK
    assert r.trace["log_entry"]["extract_output"]["action"] == "REASK"
    assert r.trace["log_entry"]["turn_result"] == "COMMITTED"


# --- extract_protections 건수 맵(ADR 0013) ---
def _cfg_counts(**over):
    cfg = base_config()
    cfg["deid"]["keep_count_map_keys"] = ["extract_protections"]
    cfg["deid"].update(over)
    return cfg


def test_count_map_default_still_dropped():
    t = make_trace(extract_protections={"G5": 1})
    assert deid().deidentify(envelope(t)).trace["extract_protections"] == {}


def test_count_map_kept_only_for_listed_field_and_safe_entries():
    t = make_trace(extract_protections={"G5": 1, "P2": 0, "홍길동": 3, "john@x.kr": 2, "g5": 1, "SENT<prot-key>": 31337},
                   response_protections={"G5": 4})
    r = deid(_cfg_counts()).deidentify(envelope(t)).trace
    assert r["extract_protections"] == {"G5": 1, "P2": 0}            # 식별자 모양 키 + 정수만
    assert r["response_protections"] == {}                           # 목록에 없는 필드는 그대로 비운다
    assert "홍길동" not in json.dumps(r, ensure_ascii=False)


def test_count_map_null_and_empty_pass():
    for v in (None, {}):
        r = deid(_cfg_counts()).deidentify(envelope(make_trace(extract_protections=v))).trace
        assert r["extract_protections"] == v


def test_count_map_key_pattern_configurable_and_fingerprint():
    t = make_trace(extract_protections={"G5": 1, "abc": 2})
    # 계약은 규칙 코드 키를 ^[A-Z][A-Z0-9_]{0,31}$로 정한다. 소문자 패턴으로 남긴 키는 자기 검사에서 닫힌다(CR-008).
    with pytest.raises(DeidError, match="OpsFeedbackDeidentifiedTrace"):
        deid(_cfg_counts(count_map_key_pattern=r"[a-z]{3}")).deidentify(envelope(t))
    assert DeidPolicy.from_config(base_config()["deid"]).fingerprint() != DeidPolicy.from_config(_cfg_counts()["deid"]).fingerprint()


def test_count_map_bad_config_rejected():
    with pytest.raises(ValueError):
        DeidPolicy.from_config(_cfg_counts(keep_count_map_keys="extract_protections")["deid"])

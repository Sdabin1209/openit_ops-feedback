"""실패 탐지(교체 가능한 Detector 인터페이스)와 사례 분류.

탐지기는 비식별을 마친 trace만 본다(원본에 접근하지 않는다). 규칙 기반으로 시작한다.
Detector 구현을 바꾸려면 설정의 detection.detectors에 `factory: "모듈:함수"`를 적는다.
분류체계(failure_type의 값)는 미정(O11)이다. 코드는 규칙 ID만 알고, 규칙 ID -> failure_type 대응은 설정이 정한다.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class Detection:
    rule_id: str
    method: str = "RULE"                 # FailureCase.detection_method
    detail: dict = field(default_factory=dict)   # 개인정보가 없는 값만(규칙 ID, 열거값, 개수)


@runtime_checkable
class Detector(Protocol):
    name: str

    def detect(self, deid_trace: dict) -> list[Detection]: ...


def _get(d: Any, *path: str) -> Any:
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def _turn_failure_signal(t: dict, params: dict) -> Optional[dict]:
    sig = _get(t, "orchestrator_output", "turn_failure_signal") or _get(t, "frontend_input", "turn_failure_signal")
    if sig:
        return {"write_committed": sig.get("write_committed"), "staff_alert": sig.get("staff_alert")}
    return None


def _failed_steps(t: dict, params: dict) -> Optional[dict]:
    tr = _get(t, "log_entry", "turn_result")
    if isinstance(tr, dict) and tr.get("failed_steps"):
        return {"steps": [f"{s.get('phase')}:{s.get('step')}" for s in tr["failed_steps"]]}
    return None


def _commit_error(t: dict, params: dict) -> Optional[dict]:
    st = _get(t, "persistence_commit_result", "status")
    if st is not None and st != "OK":
        return {"status": st}
    return None


def _generator_fallback(t: dict, params: dict) -> Optional[dict]:
    """LoRA-B 호출이 서버 대체 문장으로 끝났다(response_outcome=='fallback'). 규칙 ID는 설정 호환을 위해 옛 이름을 유지한다."""
    return {} if _get(t, "response_outcome") == "fallback" else None


def _staff_alert_failed(t: dict, params: dict) -> Optional[dict]:
    return {} if _get(t, "log_entry", "staff_alert_delivery") == "FAILURE" else None


def _intervention_failed(t: dict, params: dict) -> Optional[dict]:
    return {} if _get(t, "orchestrator_output", "intervention_result", "result") == "FAILED" else None


# 출처: survey-service design.md 18절(값이 없으면 REDIRECT)과 그 문서의 학습 데이터 전수 일치 기록. 이 영역은 데이터를 직접 보지 않았다. docs/adr/0005-detection-rules.md, README G14
DEFAULT_EMPTY_VALUES_OK_ACTIONS = ["REDIRECT"]


def _extract_failed(t: dict, params: dict) -> Optional[dict]:
    """LoRA-A 추출이 실패했거나 값 없이 끝났다.

    (1) extract_status가 있고 OK가 아니다(OUTPUT_INVALID, VIOLATION, ERROR). 값이 없는 턴(null)은 해당 없음.
    (2) 추출은 OK인데 extract_output.values가 비고 action이 값 없음을 허용하는 action(기본 REDIRECT)이 아니다.
        근거: survey-service design.md 18절에서 B 입력의 topic은 첫 값의 문항이고 값이 없으면 REDIRECT라고 했다(5,077행 전수 일치).
        즉 REDIRECT만 값이 비는 것이 정상이다. 허용 action은 params.empty_values_ok_actions로 바꾼다.
    action은 비식별에서 남겨야(keep_string_keys) 읽힌다. 마스킹돼 있으면 (2)는 보수적으로 걸린다.
    """
    st = _get(t, "extract_status")
    if st is not None and st != "OK":
        return {"reason": "STATUS", "status": st, "attempts": _get(t, "extract_attempts")}
    out = _get(t, "extract_output")
    if st == "OK" and isinstance(out, dict) and out.get("values") == []:
        ok_actions = params.get("empty_values_ok_actions", DEFAULT_EMPTY_VALUES_OK_ACTIONS)
        if out.get("action") not in ok_actions:
            return {"reason": "EMPTY_VALUES"}
    return None


def _reask_streak(t: dict, params: dict) -> Optional[dict]:
    """같은 문항에서 되묻기(REASK)가 연속으로 threshold번 이상이다. 이번 턴을 포함해 센다.

    v1의 UNRECOGNIZED_STREAK(planner 기록)를 대체한다. 세션 문서의 대화 로그(session_before.dialogue_log, 이번 턴 이전)에서
    뒤에서부터 같은 action·같은 next_item(문항)인 항목을 센다. 추출 출력이 없는 턴(비발화, 추출 실패)은 연속을 끊는다.
    params.threshold 필수(기본값 없음). params.actions(기본 ["REASK"])에 CONFIRM 등을 더할 수 있다.
    next_item이 REASK에서 되묻는 문항을 가리킨다는 것은 문서로 확인하지 못했다(미확인). 같은 값끼리 비교하므로 규칙은 그와 무관하게 일관된다.
    """
    th = params.get("threshold")
    if th is None:
        raise ValueError("REASK_STREAK 규칙은 params.threshold가 필요하다(기본값 없음)")
    actions = params.get("actions", ["REASK"])
    cur = _get(t, "extract_output")
    if not isinstance(cur, dict) or cur.get("action") not in actions or not cur.get("next_item"):
        return None
    q, act = cur["next_item"], cur["action"]
    n = 1
    log = _get(t, "session_before", "dialogue_log")
    for e in reversed(log if isinstance(log, list) else []):
        eo = e.get("extract_output") if isinstance(e, dict) else None
        if isinstance(eo, dict) and eo.get("action") == act and eo.get("next_item") == q:
            n += 1
        else:
            break
    return {"streak": n, "action": act} if n >= th else None


RULES: dict[str, Callable[[dict, dict], Optional[dict]]] = {
    "TURN_FAILURE_SIGNAL": _turn_failure_signal,
    "TURN_FAILED_STEPS": _failed_steps,
    "COMMIT_ERROR": _commit_error,
    "GENERATOR_FALLBACK": _generator_fallback,
    "STAFF_ALERT_DELIVERY_FAILED": _staff_alert_failed,
    "INTERVENTION_FAILED": _intervention_failed,
    "EXTRACT_FAILED": _extract_failed,
    "REASK_STREAK": _reask_streak,
}

# v2(D74)에서 없어진 규칙. 뜻이 달라져 이름만 바꿔 받지 않고, 바꿀 이름을 알려 준다.
RETIRED_RULES = {
    "PARSER_EMPTY": "EXTRACT_FAILED (파서 입력·항목이 없어졌다. 조건: extract_status!=OK 또는 값 없는 비REDIRECT 추출)",
    "UNRECOGNIZED_STREAK": "REASK_STREAK (planner가 없어졌다. 같은 문항 REASK 연속 횟수, params.threshold)",
}


class RuleDetector:
    name = "rule"

    def __init__(self, rules: list[dict]) -> None:
        self._rules: list[tuple[str, dict]] = []
        for r in rules:
            rid = r["id"]
            if rid in RETIRED_RULES:
                raise ValueError(f"없어진 규칙: {rid}. 대체: {RETIRED_RULES[rid]}")
            if rid not in RULES:
                raise ValueError(f"알 수 없는 규칙: {rid}")
            if r.get("enabled", True):
                self._rules.append((rid, r.get("params") or {}))
        # 설정 오류를 조기에 드러낸다
        for rid, params in self._rules:
            if rid == "REASK_STREAK" and "threshold" not in params:
                raise ValueError("REASK_STREAK 규칙은 params.threshold가 필요하다(기본값 없음)")

    def detect(self, deid_trace: dict) -> list[Detection]:
        out = []
        for rid, params in self._rules:
            detail = RULES[rid](deid_trace, params)
            if detail is not None:
                out.append(Detection(rule_id=rid, method="RULE", detail=detail))
        return out


def build_detectors(cfg: list[dict]) -> list[Detector]:
    out: list[Detector] = []
    for d in cfg:
        if d.get("type") == "rule":
            out.append(RuleDetector(d.get("rules", [])))
        elif "factory" in d:
            mod, _, fn = d["factory"].partition(":")
            det = getattr(importlib.import_module(mod), fn)(**(d.get("args") or {}))
            if not isinstance(det, Detector):
                raise ValueError(f"Detector 인터페이스를 지키지 않는다: {d['factory']}")
            out.append(det)
        else:
            raise ValueError(f"알 수 없는 탐지기 설정: {d}")
    return out


@dataclass(frozen=True)
class Classification:
    failure_type: str
    primary_rule: str


class Classifier:
    """탐지 결과 -> failure_type 문자열. 분류체계는 설정이 정한다(O11 미정)."""

    def __init__(self, cfg: dict) -> None:
        self.default = cfg.get("default_failure_type")
        if not self.default:
            raise ValueError("classification.default_failure_type이 필요하다(분류체계는 사용자 결정 전이라 값을 박지 않는다)")
        self.rule_to_type: dict[str, str] = dict(cfg.get("rule_to_type") or {})
        self.priority: list[str] = list(cfg.get("priority") or [])

    def classify(self, detections: list[Detection]) -> Classification:
        def rank(d: Detection):
            return (self.priority.index(d.rule_id) if d.rule_id in self.priority else len(self.priority), d.rule_id)
        first = sorted(detections, key=rank)[0]
        return Classification(self.rule_to_type.get(first.rule_id, self.default), first.rule_id)

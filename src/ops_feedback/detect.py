"""실패 탐지(교체 가능한 Detector 인터페이스)와 사례 분류.

탐지기는 비식별을 마친 trace만 본다(원본에 접근하지 않는다). 규칙 기반으로 시작한다.
Detector 구현을 바꾸려면 설정의 detection.detectors에 `factory: "모듈:함수"`를 적는다.
분류체계(failure_type의 값)는 미정(O11)이다. 코드는 규칙 ID만 알고, 규칙 ID -> failure_type 대응은 설정이 정한다.
"""
from __future__ import annotations

import importlib
import logging
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


log = logging.getLogger("detect")

PAUSE_REASONS = ("RISK", "REASK_LIMIT", "OTHER")
_PAUSE_PATHS = (("orchestrator_output", "turn_failure_signal"), ("frontend_input", "turn_failure_signal"),
                ("log_entry", "turn_failure_signal"), ("orchestrator_output", "log_entry", "turn_failure_signal"))


def pause_reason_of(t: dict) -> Optional[str]:
    """이 턴의 정지 사유(RISK|REASK_LIMIT|OTHER). 없으면 None: 구버전 trace(필드 없음), 정지하지 않은 턴, 비식별 정책이 남기지 않은 경우.
    모르는 값은 OTHER로 읽는다(boundary-contracts PUBLIC_API H절). None을 어떤 사유로 추정하지 않는다(CR-H002이 추정을 버린 이유)."""
    for path in _PAUSE_PATHS:
        v = _get(t, *path, "pause_reason")
        if v is not None:
            return v if v in PAUSE_REASONS else "OTHER"
    return None


def _turn_failure_signal(t: dict, params: dict) -> Optional[dict]:
    sig = _get(t, "orchestrator_output", "turn_failure_signal") or _get(t, "frontend_input", "turn_failure_signal")
    if sig:
        d = {"write_committed": sig.get("write_committed"), "staff_alert": sig.get("staff_alert")}
        pr = pause_reason_of(t)
        if pr is not None:
            d["pause_reason"] = pr
        return d
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


def _response_fallback(t: dict, params: dict) -> Optional[dict]:
    """LoRA-B 호출이 서버 대체 문장으로 끝났다(response_outcome=='fallback'). 규칙 ID는 v1 이름 GENERATOR_FALLBACK에서 바꿨다(ADR 0015)."""
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
    """되묻기 한도 정지: 서비스가 이 턴에 REASK_LIMIT로 정지했다고 trace에 남겼다(pause_reason). 연속 횟수는 다시 세지 않는다.
    왜: 몇 번 되물으면 멈출지는 survey-service의 판단(상한 설정, 문항 세트의 limit 중 최솟값)이다. 이웃 판단을 재구현하면 상한이 바뀔 때
    어긋나고 실제로 한 칸 어긋나 있었다(구조 규칙 R7, 점검 OF-02, ADR 0019). params(threshold, actions)는 무시한다(폐기 예정)."""
    if pause_reason_of(t) == "REASK_LIMIT":
        return {"pause_reason": "REASK_LIMIT", "service_escalated": True}
    return None


RULES: dict[str, Callable[[dict, dict], Optional[dict]]] = {
    "TURN_FAILURE_SIGNAL": _turn_failure_signal,
    "TURN_FAILED_STEPS": _failed_steps,
    "COMMIT_ERROR": _commit_error,
    "RESPONSE_FALLBACK": _response_fallback,
    "STAFF_ALERT_DELIVERY_FAILED": _staff_alert_failed,
    "INTERVENTION_FAILED": _intervention_failed,
    "EXTRACT_FAILED": _extract_failed,
    "REASK_STREAK": _reask_streak,
}

# v2(D74)에서 없어진 규칙. 뜻이 달라져 이름만 바꿔 받지 않고, 바꿀 이름을 알려 준다.
RETIRED_RULES = {
    "PARSER_EMPTY": "EXTRACT_FAILED (파서 입력·항목이 없어졌다. 조건: extract_status!=OK 또는 값 없는 비REDIRECT 추출)",
    "GENERATOR_FALLBACK": "RESPONSE_FALLBACK (v1 이름. 뜻은 같다: response_outcome==fallback. ADR 0015)",
    "UNRECOGNIZED_STREAK": "REASK_STREAK (planner가 없어졌다. 서비스가 pause_reason=REASK_LIMIT로 남긴 턴)",
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
            en = r.get("enabled", True)
            if not isinstance(en, bool):         # 문자열 "false"는 참으로 평가되어 끈 규칙이 켜진다
                raise ValueError(f"규칙 {rid}의 enabled는 true 또는 false(불리언)여야 한다")
            if en:
                self._rules.append((rid, r.get("params") or {}))
        for rid, params in self._rules:
            if rid == "REASK_STREAK" and params:
                log.warning("REASK_STREAK의 params(threshold, actions)는 무시된다(서비스가 trace에 남긴 pause_reason을 읽는다, ADR 0019). 폐기 예정")

    def detect(self, deid_trace: dict) -> list[Detection]:
        out = []
        for rid, params in self._rules:
            detail = RULES[rid](deid_trace, params)
            if detail is not None:
                out.append(Detection(rule_id=rid, method="RULE", detail=detail))
        return _merge_service_escalation(out)


def _merge_service_escalation(dets: list[Detection]) -> list[Detection]:
    """서비스가 이미 REASK_LIMIT로 직원을 호출한 턴은 같은 사건이 TURN_FAILURE_SIGNAL과 REASK_STREAK 둘로 잡힌다.
    둘을 별개 탐지로 두면 한 사건이 두 번 센다. 신호 탐지 하나로 합친다(ADR 0014).
    신호가 없는 턴(REASK_STREAK만 걸림)은 합칠 대상이 없어 그대로 둔다."""
    by = {d.rule_id: d for d in dets}
    sig, streak = by.get("TURN_FAILURE_SIGNAL"), by.get("REASK_STREAK")
    if sig is None or streak is None or not streak.detail.get("service_escalated"):
        return dets
    merged = Detection(rule_id=sig.rule_id, method=sig.method,
                       detail={**sig.detail, "service_escalated": True})
    return [merged if d is sig else d for d in dets if d is not streak]


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

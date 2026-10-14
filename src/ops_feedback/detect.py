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
    return {} if _get(t, "generator_output", "is_fallback") is True else None


def _staff_alert_failed(t: dict, params: dict) -> Optional[dict]:
    return {} if _get(t, "log_entry", "staff_alert_delivery") == "FAILURE" else None


def _intervention_failed(t: dict, params: dict) -> Optional[dict]:
    return {} if _get(t, "orchestrator_output", "intervention_result", "result") == "FAILED" else None


def _parser_empty(t: dict, params: dict) -> Optional[dict]:
    if _get(t, "parser_input") is not None and _get(t, "parser_output", "items") == []:
        return {}
    return None


def _unrecognized_streak(t: dict, params: dict) -> Optional[dict]:
    th = params.get("threshold")
    if th is None:
        raise ValueError("UNRECOGNIZED_STREAK 규칙은 params.threshold가 필요하다(기본값 없음)")
    n = _get(t, "planner_output", "planner_record_update", "unrecognized_streak")
    if isinstance(n, int) and n >= th:
        return {"streak": n}
    return None


RULES: dict[str, Callable[[dict, dict], Optional[dict]]] = {
    "TURN_FAILURE_SIGNAL": _turn_failure_signal,
    "TURN_FAILED_STEPS": _failed_steps,
    "COMMIT_ERROR": _commit_error,
    "GENERATOR_FALLBACK": _generator_fallback,
    "STAFF_ALERT_DELIVERY_FAILED": _staff_alert_failed,
    "INTERVENTION_FAILED": _intervention_failed,
    "PARSER_EMPTY": _parser_empty,
    "UNRECOGNIZED_STREAK": _unrecognized_streak,
}


class RuleDetector:
    name = "rule"

    def __init__(self, rules: list[dict]) -> None:
        self._rules: list[tuple[str, dict]] = []
        for r in rules:
            rid = r["id"]
            if rid not in RULES:
                raise ValueError(f"알 수 없는 규칙: {rid}")
            if r.get("enabled", True):
                self._rules.append((rid, r.get("params") or {}))
        # 설정 오류를 조기에 드러낸다
        for rid, params in self._rules:
            if rid == "UNRECOGNIZED_STREAK" and "threshold" not in params:
                raise ValueError("UNRECOGNIZED_STREAK 규칙은 params.threshold가 필요하다(기본값 없음)")

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

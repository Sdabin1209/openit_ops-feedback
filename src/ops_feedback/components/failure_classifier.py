"""탐지 결과 -> failure_type. 입력 ClassifyRequest, 출력 Classification. 분류체계는 설정이 정한다(O11 미정): 코드는 규칙 ID만 알고 규칙 ID -> failure_type 대응은 설정이다."""
from __future__ import annotations

from ..types.Classification import Classification
from ..types.ClassifyRequest import ClassifyRequest
from ..wiring.validate import check, check_part


class FailureClassifier:
    def __init__(self, cfg: dict) -> None:
        # default_failure_type이 필요하다(분류체계는 사용자 결정 전이라 값을 박지 않는다): 필수·형식은 계약 OpsFeedbackJobConfig.classification이 정한다.
        check_part("OpsFeedbackJobConfig", "/properties/classification", cfg, "분류 설정")
        self.default = cfg["default_failure_type"]
        self.rule_to_type: dict[str, str] = dict(cfg.get("rule_to_type") or {})
        self.priority: list[str] = list(cfg.get("priority") or [])

    def classify(self, request: ClassifyRequest) -> Classification:
        check("ClassifyRequest", request)

        def rank(d: dict):
            return (self.priority.index(d["rule_id"]) if d["rule_id"] in self.priority else len(self.priority), d["rule_id"])
        first = sorted(request["detections"], key=rank)[0]
        out: Classification = {"failure_type": self.rule_to_type.get(first["rule_id"], self.default), "primary_rule": first["rule_id"]}
        check("Classification", out)
        return out

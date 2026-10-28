"""비식별 처리기. 원본(로그 저장소)을 읽는 유일한 컴포넌트.

방식: 문진 스키마의 TurnTrace 정의를 따라 걸으며 '허용한 것만 남긴다'(허용 목록 방식).
- 스키마가 열거값·상수로 정한 문자열은 남긴다(구조 정보).
- 자유 문자열은 정책이 키 이름으로 허용한 것만 남기고 나머지는 마스킹 토큰으로 바꾼다.
- 스키마에 없는 키, 구조가 없는 자유 객체, 어느 가지에도 맞지 않는 값은 제거한다(보수적 제거).
- 정책이 지목한 정의(예: Value)는 통째로 마스킹하거나 제거한다.
- 마지막에 잔존 패턴 검사를 하고 걸리면 실패로 닫는다(DeidError). 이 경우 FailureCase를 만들지 않는다.
정책 값(허용 키, 정의 목록, 패턴, 처리 방식)은 코드에 박지 않고 설정으로 받는다.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from boundary_contracts.canonical import sha256_hash

from .logstore import DeidentifierView
from .schemas import trace_schema

# 잔존 패턴 검사의 창 크기와 겹침. 문진 스키마(TurnTrace)의 문자열에는 maxLength가 없어(읽어서 확인) 정상 trace에도 scrub_string_keys 등으로 긴 문자열이
# 남을 수 있다(수집 본문 상한 8 MiB까지). 그래서 긴 문자열을 거부하지 않고 창으로 나눠 검사한다: 설정의 정규식이 입력 길이에 선형이라는 보장이 없으므로
# 한 번에 보는 길이를 묶는 것이 목적이다(점검 round2 M2). 창 크기·겹침은 측정하지 않은 값이며 docs/adr/0011-deid-long-string-windows.md.
# 겹침(1,024)은 예시 설정의 잔존 패턴이 낼 수 있는 가장 긴 일치(이메일 약 64+1+255+1+TLD)보다 크다. 이보다 긴 일치를 내는 패턴은 창 경계에서 놓칠 수 있다.
SCAN_WINDOW = 4096
SCAN_OVERLAP = 1024
MASK = "[MASKED]"
DROPPED = "[DROPPED]"
_DROP = object()
_PSEUDONYM = re.compile(r"p-[0-9a-f]{32}")


# 출처: docs/adr/0004-deid-allowlist.md. 패턴의 길이 한도는 근거 기록 없는 보수적 임의값이다.
# 값 검증 기본 패턴. keep_string_keys에 든 키의 값은 이 패턴(전체 일치)에 맞을 때만 남고, 맞지 않으면 마스킹한다(원문을 보존하지 않는다).
# 이 키들은 문항 ID·슬롯 키·코드·action처럼 식별자 모양이어야 하는 값인데, 위반 턴에서는 모델이 임의 문자열(예: 사람 이름이 든 문장)을
# 낼 수 있어서 키 이름만으로 통과시키면 개인정보가 남는다. 설정의 `keep_string_patterns`로 키별로 바꿀 수 있다.
# 한계: 식별자 모양의 영문 이름("John")은 구별할 수 없다. 한국어·공백·기호가 든 값은 모두 걸러진다.
DEFAULT_KEEP_PATTERNS = {
    "question_id": r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}",
    "next_item": r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}",
    "current_question_id": r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}",
    "slot_id": r"[A-Za-z][A-Za-z0-9_\-]{0,63}",
    "repeat_key": r"[A-Za-z0-9_\-]{0,64}",
    "code": r"[A-Z][A-Z0-9_]{0,63}",
    "action": r"[A-Z][A-Z0-9_]{0,31}",
    "path": r"[A-Za-z0-9_\[\]\.\-]{0,160}",
}


# 출처: docs/adr/0013-extract-protections-counts.md. 건수 맵(규칙 코드 -> 건수)의 키 모양. 코드는 model-runtime이 정하며(boundary-contracts HTTP.md의 예: G5, G2) 계약 밖이라
# 대문자로 시작하는 짧은 식별자 모양만 남긴다. 길이 한도 32는 근거 기록 없는 보수적 임의값이다. 설정 `deid.count_map_key_pattern`으로 바꾼다.
DEFAULT_COUNT_MAP_KEY_PATTERN = r"[A-Z][A-Z0-9_]{0,31}"


class DeidError(Exception):
    """비식별 결과에 개인정보 의심 패턴이 남았다. 결과를 버린다(fail-closed)."""


@dataclass(frozen=True)
class DeidPolicy:
    policy_id: str
    keep_string_keys: frozenset
    scrub_string_keys: frozenset
    pseudonymize_keys: frozenset
    mask_defs: frozenset
    drop_defs: frozenset
    mask_number_keys: frozenset
    mask_value_keys: frozenset           # 타입과 상관없이(숫자·불리언·객체·배열 포함) 값을 통째로 마스킹할 키
    unlisted_string_action: str          # "mask" | "drop"
    residual_patterns: dict
    scrub_replacements: dict
    keep_patterns: dict = field(default_factory=dict)     # 키 -> 전체 일치 정규식(기본 + 설정)
    count_map_keys: frozenset = frozenset()               # 건수 맵(키 -> 정수)으로 남길 필드 이름(예: extract_protections). 기본은 비어 있어 자유 객체로 비운다
    count_map_key_pattern: str = DEFAULT_COUNT_MAP_KEY_PATTERN
    keep_pause_reason: bool = False                       # 정지 사유 열거(RISK|REASK_LIMIT|OTHER)를 남긴다. docs/adr/0014

    def unvalidated_keep_keys(self) -> list:
        """값 검증 패턴이 없는 keep 키. 값이 그대로 통과하므로 시작 때 경고한다."""
        return sorted(k for k in self.keep_string_keys if k not in self.keep_patterns)

    @classmethod
    def from_config(cls, cfg: dict) -> "DeidPolicy":
        for k in ("policy_id", "keep_string_keys", "unlisted_string_action"):
            if k not in cfg:
                raise ValueError(f"deid 설정에 {k}가 없다. 비식별 기준은 사용자가 정하며 기본값이 없다.")
        if cfg["unlisted_string_action"] not in ("mask", "drop"):
            raise ValueError("deid.unlisted_string_action은 mask 또는 drop")
        pats = cfg.get("residual_patterns") or {}
        for name, p in pats.items():
            re.compile(p)
        kp = dict(DEFAULT_KEEP_PATTERNS)
        kp.update(cfg.get("keep_string_patterns") or {})
        for name, p in kp.items():
            re.compile(p)
        cmk = cfg.get("keep_count_map_keys") or []
        if not isinstance(cmk, list) or not all(isinstance(x, str) for x in cmk):
            raise ValueError("deid.keep_count_map_keys는 필드 이름의 목록이어야 한다")
        kpr = cfg.get("keep_pause_reason", False)
        if not isinstance(kpr, bool):
            raise ValueError("deid.keep_pause_reason는 true 또는 false(불리언)여야 한다")
        cmp_ = cfg.get("count_map_key_pattern", DEFAULT_COUNT_MAP_KEY_PATTERN)
        re.compile(cmp_)
        return cls(
            policy_id=str(cfg["policy_id"]),
            keep_string_keys=frozenset(cfg.get("keep_string_keys") or []),
            scrub_string_keys=frozenset(cfg.get("scrub_string_keys") or []),
            pseudonymize_keys=frozenset(cfg.get("pseudonymize_keys") or []),
            mask_defs=frozenset(cfg.get("mask_defs") or []),
            drop_defs=frozenset(cfg.get("drop_defs") or []),
            mask_number_keys=frozenset(cfg.get("mask_number_keys") or []),
            mask_value_keys=frozenset(cfg.get("mask_value_keys") or []),
            unlisted_string_action=cfg["unlisted_string_action"],
            residual_patterns=dict(pats),
            scrub_replacements=dict(cfg.get("scrub_patterns") or {}),
            keep_patterns={k: v for k, v in kp.items() if k in frozenset(cfg.get("keep_string_keys") or [])},
            count_map_keys=frozenset(cmk),
            count_map_key_pattern=cmp_,
            keep_pause_reason=kpr,
        )

    def fingerprint(self) -> str:
        extra = {"count_map_keys": sorted(self.count_map_keys), "count_map_key_pattern": self.count_map_key_pattern} if self.count_map_keys else {}
        if self.keep_pause_reason:
            extra = {**extra, "keep_pause_reason": True}      # 켠 설정만 지문에 넣어, 안 쓰는 기존 설정의 지문은 그대로다(ADR 0013과 같은 방식)
        return sha256_hash({**extra,
            "policy_id": self.policy_id, "keep": sorted(self.keep_string_keys), "scrub": sorted(self.scrub_string_keys),
            "pseudo": sorted(self.pseudonymize_keys), "mask_defs": sorted(self.mask_defs), "drop_defs": sorted(self.drop_defs),
            "num": sorted(self.mask_number_keys), "mask_value_keys": sorted(self.mask_value_keys), "unlisted": self.unlisted_string_action,
            "residual": self.residual_patterns, "scrub_patterns": self.scrub_replacements, "keep_patterns": self.keep_patterns})


# 정지 사유(boundary-contracts 2.1.0, CR-H002)가 실릴 수 있는 자리(문진 스키마의 TurnFailureSignal). 스키마 걷기는 열거에 없는 값을 마스킹하고
# 스키마가 이 필드를 모르는 옛 스냅샷에서는 버리므로, 걷기 밖에서 원본 값을 열거값으로 정규화해 같은 자리에 다시 넣는다(모르는 값은 OTHER).
PAUSE_REASONS = ("RISK", "REASK_LIMIT", "OTHER")
PAUSE_REASON_PATHS = (("frontend_input", "turn_failure_signal"), ("orchestrator_output", "turn_failure_signal"),
                      ("log_entry", "turn_failure_signal"), ("orchestrator_output", "log_entry", "turn_failure_signal"))


def normalize_pause_reason(v: Any) -> Optional[str]:
    """열거값이면 그대로, 값이 있으나 모르는 값이면 OTHER(계약: 읽는 쪽은 모르는 값을 OTHER로). 없음(None)이면 None. 문자열 원문은 남기지 않는다."""
    if v is None:
        return None
    return v if isinstance(v, str) and v in PAUSE_REASONS else "OTHER"


def _node(d: Any, path: tuple):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d if isinstance(d, dict) else None


@dataclass
class DeidResult:
    trace: dict
    stats: dict
    policy_fingerprint: str


def _jtype(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    return "object"


class _Walker:
    def __init__(self, defs: dict, policy: DeidPolicy, salt: str) -> None:
        self.defs = defs
        self.p = policy
        self.salt = salt
        self.stats: Counter = Counter()
        self._scrub = {n: re.compile(pat) for n, pat in policy.scrub_replacements.items()}
        self._keep = {k: re.compile(p) for k, p in policy.keep_patterns.items()}
        self._count_key = re.compile(policy.count_map_key_pattern)

    # --- 스키마 해석 ---
    def _resolve(self, node: dict, names: tuple = ()) -> tuple[dict, tuple]:
        while "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            names += (name,)
            node = self.defs[name]
        return node, names

    def _match(self, node: dict, data: Any) -> bool:
        node, _ = self._resolve(node)
        if "const" in node:
            return data == node["const"]
        if "enum" in node:
            return data in node["enum"]
        for k in ("oneOf", "anyOf"):
            if k in node:
                return any(self._match(b, data) for b in node[k])
        t = node.get("type")
        if t is not None:
            ts = t if isinstance(t, list) else [t]
            jt = _jtype(data)
            if not (jt in ts or (jt == "integer" and "number" in ts)):
                return False
        if isinstance(data, dict) and "properties" in node:
            if not set(node.get("required", [])) <= set(data):
                return False
            if node.get("additionalProperties") is False and not set(data) <= set(node["properties"]):
                return False
            # 상수 판별자(예: kind)가 있으면 맞는지 본다
            for k, sub in node["properties"].items():
                if k in data:
                    s, _ = self._resolve(sub)
                    if "const" in s and data[k] != s["const"]:
                        return False
        return True

    # --- 걷기 ---
    def walk(self, data: Any, node: dict, key: str, path: str) -> Any:
        node, names = self._resolve(node)
        for n in names:
            if n in self.p.drop_defs:
                self.stats["dropped_by_def"] += 1
                self.stats[f"path_dropped:{path}"] += 1
                return DROPPED
            if n in self.p.mask_defs:
                self.stats["masked_by_def"] += 1
                self.stats[f"path_masked:{path}"] += 1
                return MASK
        for k in ("oneOf", "anyOf"):
            if k in node:
                for b in node[k]:
                    if self._match(b, data):
                        return self.walk(data, b, key, path)
                return self._unknown(data, path)
        if "const" in node or "enum" in node:
            return data if self._match(node, data) else self._unknown(data, path)
        if data is None:
            return None
        if key in self.p.mask_value_keys:
            # 스키마가 형태를 정하지 않은 값(예: 모델이 낸 추출 값)은 숫자·불리언도 환자 답변일 수 있다
            self.stats["masked_by_key"] += 1
            self.stats[f"path_masked:{path}"] += 1
            return MASK
        if isinstance(data, str):
            return self._string(data, key, path)
        if isinstance(data, bool):
            return data
        if isinstance(data, (int, float)):
            if key in self.p.mask_number_keys:
                self.stats["masked_numbers"] += 1
                self.stats[f"path_masked:{path}"] += 1
                return None
            return data
        if isinstance(data, list):
            items = node.get("items")
            if items is None:
                self.stats["dropped_free_objects"] += 1
                return []
            out = []
            for v in data:
                r = self.walk(v, items, key, path + "[]")
                out.append(MASK if r is _DROP else r)
            return out
        if isinstance(data, dict):
            props = node.get("properties")
            if not props and key in self.p.count_map_keys and self._is_count_map(node):
                return self._count_map(data, path)
            if not props:
                self.stats["dropped_free_objects"] += 1
                self.stats[f"path_dropped:{path}"] += 1
                return {}
            out = {}
            for k, v in data.items():
                if k not in props:
                    self.stats["dropped_unknown_keys"] += 1   # 키 이름도 값일 수 있어 기록하지 않는다
                    continue
                r = self.walk(v, props[k], k, f"{path}.{k}" if path else k)
                if r is not _DROP:
                    out[k] = r
            return out
        return self._unknown(data, path)

    def _is_count_map(self, node: dict) -> bool:
        """스키마가 값 형태를 정수(additionalProperties)로 정한 맵인가. 아니면 건수 맵으로 다루지 않고 기존대로 비운다."""
        ap = node.get("additionalProperties")
        if not isinstance(ap, dict):
            return False
        ap, _ = self._resolve(ap)
        return ap.get("type") == "integer"

    def _count_map(self, data: dict, path: str) -> dict:
        """키가 식별자 모양이고 값이 0 이상의 정수인 항목만 남긴다. 그 밖의 항목은 키 이름도 값일 수 있어 기록 없이 버린다."""
        out = {}
        for k, v in data.items():
            if isinstance(k, str) and self._count_key.fullmatch(k) and isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                out[k] = v
            else:
                self.stats["dropped_unknown_keys"] += 1
        return out

    def _unknown(self, data: Any, path: str) -> Any:
        self.stats["dropped_unmatched"] += 1
        self.stats[f"path_dropped:{path}"] += 1
        return MASK if isinstance(data, str) else _DROP

    def _string(self, s: str, key: str, path: str) -> Any:
        if key in self.p.pseudonymize_keys:
            self.stats["pseudonymized"] += 1
            return "p-" + sha256_hash({"salt": self.salt, "key": key, "value": s})[7:39]
        if key in self.p.scrub_string_keys:
            for name, rx in self._scrub.items():
                s, n = rx.subn(f"[{name}]", s)
                self.stats["scrubbed"] += n
            return s
        if key in self.p.keep_string_keys:
            rx = self._keep.get(key)
            if rx is not None and not rx.fullmatch(s):       # 식별자 모양이 아니면 원문을 남기지 않는다
                self.stats["masked_by_pattern"] += 1
                self.stats[f"path_masked:{path}"] += 1
                return MASK
            return s
        self.stats["masked_strings"] += 1
        self.stats[f"path_masked:{path}"] += 1
        return MASK if self.p.unlisted_string_action == "mask" else _DROP


def _leaf_strings(v: Any) -> Iterator[str]:
    if isinstance(v, str):
        yield v
    elif isinstance(v, list):
        for x in v:
            yield from _leaf_strings(x)
    elif isinstance(v, dict):
        for x in v.values():
            yield from _leaf_strings(x)


class Deidentifier:
    """원본을 읽는 유일한 컴포넌트. view는 LogStore.grant_deidentifier_view()로 받는다."""

    def __init__(self, policy: DeidPolicy, salt: str, view: Optional[DeidentifierView] = None) -> None:
        if not salt:
            raise ValueError("의사 식별자용 salt가 비어 있다")
        self.policy = policy
        self._salt = salt
        self._view = view
        self._residual = {n: re.compile(p) for n, p in policy.residual_patterns.items()}

    def pending(self, limit: Optional[int] = None, after_seq: int = 0) -> list[dict]:
        """아직 처리하지 않은 원본 봉투(row: envelope, received_at, ...). 원본을 읽는 유일한 경로. seq가 after_seq보다 큰 것만, 오름차순."""
        if self._view is None:
            raise RuntimeError("원본 읽기 권한이 없다")
        return self._view.read_unprocessed(limit, after_seq)

    def deidentify(self, envelope: dict) -> DeidResult:
        ver = envelope["runtime_contract_version"]
        schema = trace_schema(ver)
        defs = schema["$defs"]
        stale = sorted((self.policy.mask_defs | self.policy.drop_defs) - set(defs))
        if stale:
            # 문진 스키마가 바뀌어 정책이 지목한 정의가 사라지면 조용히 가리지 않게 되므로 실패로 닫는다
            raise DeidError(f"정책이 지목한 정의가 문진 스키마에 없다: {stale}")
        w = _Walker(defs, self.policy, self._salt)
        out = w.walk(envelope["trace"], {"$ref": "#/$defs/TurnTrace"}, "", "")
        if not isinstance(out, dict):
            raise DeidError("trace 전체가 제거되었다")
        for path in PAUSE_REASON_PATHS:
            src, dst = _node(envelope["trace"], path), _node(out, path)
            if dst is None:
                continue
            pr = normalize_pause_reason(src.get("pause_reason")) if src is not None and self.policy.keep_pause_reason else None
            if pr is not None:
                dst["pause_reason"] = pr
            else:
                dst.pop("pause_reason", None)       # 정책이 끄면 스키마가 아는 값도 남기지 않는다(지문이 다른 정책끼리 결과가 같아야 한다)
        for s in _leaf_strings(out):
            if s in (MASK, DROPPED) or _PSEUDONYM.fullmatch(s):
                continue          # 의사 식별자는 16진 해시라 숫자열이 우연히 전화번호 패턴에 걸릴 수 있다
            for name, rx in self._residual.items():
                if any(rx.search(s[i:i + SCAN_WINDOW]) for i in range(0, max(len(s) - SCAN_OVERLAP, 1), SCAN_WINDOW - SCAN_OVERLAP)):
                    raise DeidError(f"비식별 결과에 잔존 패턴({name})이 있다")
        stats = {k: v for k, v in w.stats.items() if not k.startswith("path_")}
        stats["masked_paths"] = sorted(k[len("path_masked:"):] for k in w.stats if k.startswith("path_masked:"))
        stats["dropped_paths"] = sorted(k[len("path_dropped:"):] for k in w.stats if k.startswith("path_dropped:"))
        return DeidResult(trace=out, stats=stats, policy_fingerprint=self.policy.fingerprint())

    def pseudonym(self, key: str, value: str) -> str:
        return "p-" + sha256_hash({"salt": self._salt, "key": key, "value": value})[7:39]

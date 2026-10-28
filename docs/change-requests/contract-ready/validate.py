"""contract-ready 스키마를 예시로 검증한다. 사용(리포 루트): .venv/bin/python docs/change-requests/contract-ready/validate.py
valid/ 는 모두 통과해야 하고, invalid/ 는 모두 정해진 이유로 거부되어야 한다. MANIFEST.json과 파일이 일치하는지도 본다. 하나라도 어긋나면 종료 코드 1."""
import json, sys
from pathlib import Path

from boundary_contracts import schema_dir
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

HERE = Path(__file__).resolve().parent
BASE = "file:///boundary/"
reg = Registry()
for p in sorted(schema_dir().glob("*.schema.json")):                       # 계약의 _common 등
    reg = reg.with_resource(BASE + p.name, Resource.from_contents(json.loads(p.read_text("utf-8")), DRAFT202012))
schemas = {}
for p in sorted((HERE / "schemas").glob("*.schema.json")):
    s = json.loads(p.read_text("utf-8"))
    Draft202012Validator.check_schema(s)
    assert s["$id"] == p.name and s["title"] == p.name[:-len(".schema.json")], p.name    # $id는 상대 이름, title은 계약 이름
    schemas[s["title"]] = s
    reg = reg.with_resource(BASE + p.name, Resource.from_contents(s, DRAFT202012))


def errors(name, doc):
    v = Draft202012Validator({"$ref": BASE + name + ".schema.json"}, registry=reg, format_checker=FormatChecker())
    return list(v.iter_errors(doc))


def messages(errs):
    for e in errs:
        yield e.message
        yield from messages(e.context)


# invalid 파일 이름 변형 -> 거부 이유로 기대하는 문자열
WHY = {
    "original-session-id": "s-001", "utterance-leak": "홍길동", "unknown-key": "patient_name", "bad-pause-reason": "BORED",
    "missing-required": "extract_status", "no-primary-rule": "primary_rule", "bad-reviewed-by": "김 검토자",
    "typo-key": "raw_dayz", "unknown-top-level": "rollbak", "reask-no-threshold": "threshold", "retention-null": "None",
    "auto-submit-string": "false", "no-processed": "processed", "raw-turn-id": "t-1", "ready-without-counters": "counters",
    "unknown-warning": "X", "draft-store-name": "postgres", "typo-name": "OPS_RAW_RETENTON_DAYS", "unknown-build-arg": "COMMIT_SHA", "bad-schema-mode": "skip",
}
bad = ok_valid = ok_invalid = 0
seen_v, seen_i = set(), set()
for kind in ("valid", "invalid"):
    for p in sorted((HERE / "examples" / kind).glob("*.json")):
        name, _, variant = p.stem.partition(".")
        if name not in schemas:
            print(f"FAIL {kind}/{p.name}: 스키마 {name} 없음"); bad += 1; continue
        errs = errors(name, json.loads(p.read_text("utf-8")))
        if kind == "valid":
            good = not errs
            ok_valid += good; seen_v.add(name)
            print(f"{'OK  ' if good else 'FAIL'} valid   {p.name} -> " + ("통과" if good else "예상 밖 거부: " + errs[0].message[:150]))
        else:
            why = WHY[variant]
            text = " ".join(messages(errs))
            good = bool(errs) and why in text
            ok_invalid += good; seen_i.add(name)
            print(f"{'OK  ' if good else 'FAIL'} invalid {p.name} -> " + (("거부됨: " + errs[0].message[:90]) if errs else "(거부 안 됨)") + ("" if good else f" [기대한 이유 '{why}' 없음]"))
        bad += not good
manifest = json.loads((HERE / "MANIFEST.json").read_text("utf-8"))["entries"]
names = {e["name"] for e in manifest}
problems = []
if names != set(schemas): problems.append(f"MANIFEST와 schemas 불일치: {sorted(names ^ set(schemas))}")
if seen_v != set(schemas): problems.append(f"valid 예시 없는 스키마: {sorted(set(schemas) - seen_v)}")
if seen_i != set(schemas): problems.append(f"invalid 예시 없는 스키마: {sorted(set(schemas) - seen_i)}")
for e in manifest:
    if set(e) < {"name", "source_draft", "producer", "consumers", "unknown_keys", "note"} or e["unknown_keys"] not in ("reject", "ignore"):
        problems.append(f"MANIFEST 항목 형식: {e['name']}")
    if not (HERE.parents[2] / e["source_draft"]).exists(): problems.append(f"source_draft 없음: {e['name']}")
for m in problems: print("FAIL", m)
bad += len(problems)
print(f"\n스키마 {len(schemas)}개, 통과(valid) {ok_valid}건, 거부(invalid) {ok_invalid}건, 어긋남 {bad}건")
sys.exit(1 if bad else 0)

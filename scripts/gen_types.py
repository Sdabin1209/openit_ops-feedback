#!/usr/bin/env python3
"""schemas/ -> src/ops_feedback/types/ 코드 타입 생성(DESIGN.md 3절). 손으로 고치지 않는다.

  .venv/bin/python scripts/gen_types.py          # 다시 생성
  .venv/bin/python scripts/gen_types.py --check  # 생성 결과가 파일과 같은지만 본다(tests/test_structure.py가 쓴다)

계약(boundary-contracts) 타입을 가리키는 `$ref`는 영역 안에서 코드 타입을 만들 수 없으므로(계약 타입은 계약 패키지가 정한다, 구조 규칙 6)
생성용 사본에서만 `{"type": "object"}`(계약 객체) 또는 해당 값 형식으로 바꾼다. 검증은 원본 스키마가 하며 이 치환은 타입 힌트에만 영향이 있다.
생성기: datamodel-code-generator(requirements-dev.lock에 고정), 출력 TypedDict(런타임 의존 없음).
"""
import json
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "ops_feedback" / "types"
SCH = ROOT / "schemas"
BREF = re.compile(r"^\.\./boundary/")


def _loosen(node):
    if isinstance(node, dict):
        r = node.get("$ref")
        if isinstance(r, str) and BREF.match(r):
            frag = r.split("#", 1)[1] if "#" in r else ""
            out = {k: v for k, v in node.items() if k != "$ref"}
            if frag.endswith("/Id") or frag.endswith("/Hash") or frag.endswith("/Timestamp"):
                out["type"] = "string"
            elif frag.endswith("/RuntimeContractVersion"):
                out["type"] = "integer"
            else:
                out["type"] = "object"
            return out
        if isinstance(r, str) and r.endswith(".schema.json") and not r.startswith("#"):
            return {**{k: v for k, v in node.items() if k != "$ref"}, "$ref": r[: -len(".schema.json")] + ".json"}
        return {k: _loosen(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_loosen(v) for v in node]
    return node


def generate(dest: pathlib.Path) -> None:
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t) / "in"
        tmp.mkdir()
        for p in sorted(SCH.glob("*.schema.json")):
            (tmp / (p.name[: -len(".schema.json")] + ".json")).write_text(json.dumps(_loosen(json.loads(p.read_text("utf-8"))), ensure_ascii=False), "utf-8")
        dest.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "datamodel_code_generator", "--input", str(tmp), "--input-file-type", "jsonschema",
                        "--output", str(dest), "--output-model-type", "typing.TypedDict", "--target-python-version", "3.10",
                        "--disable-timestamp", "--use-standard-collections", "--use-union-operator", "--formatters", "black", "isort"], check=True)


def main() -> int:
    if "--check" in sys.argv:
        with tempfile.TemporaryDirectory() as t:
            d = pathlib.Path(t) / "types"
            generate(d)
            mine = {p.relative_to(d): p.read_bytes() for p in d.rglob("*.py")}
            have = {p.relative_to(OUT): p.read_bytes() for p in OUT.rglob("*.py")}
            if mine != have:
                print("types/가 schemas/에서 다시 생성한 결과와 다르다: gen_types.py를 실행한다", file=sys.stderr)
                return 1
        return 0
    for p in OUT.rglob("*.py"):
        p.unlink()
    generate(OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())

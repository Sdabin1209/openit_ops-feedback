"""영역 구조 검사(헤드 DESIGN.md 6절, D117). 헤드의 head/scripts/structure-check.py와 같은 기준을 이 리포 안에서 강제한다."""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "src" / "ops_feedback"
HTTP_PY = re.compile(r"^\s*(from|import)\s+(boundary_contracts\.clients|openit_link\.clients|urllib\.request|http\.client|http\.server|requests|waitress|wsgiref)\b", re.M)
ENTRY = re.compile(r"(^|/)(__main__|main|app|server|config|settings|migrate|cli|index)\.py$|(^|/)migrations?/")


def code_files():
    return [p for p in PKG.rglob("*.py") if p.name != "__init__.py" and "__pycache__" not in p.parts]


def test_1_four_folders_exist():
    for d in (ROOT / "schemas", PKG / "types", PKG / "components", PKG / "wiring"):
        assert d.is_dir() and any(d.iterdir()), d


def test_2_components_do_not_import_each_other():
    names = {p.stem for p in (PKG / "components").glob("*.py") if p.stem != "__init__"}
    bad = []
    for p in (PKG / "components").glob("*.py"):
        t = p.read_text("utf-8")
        for m in re.finditer(r"^\s*from\s+\.(\w+)\s+import", t, re.M):          # 같은 폴더의 형제 모듈
            if m.group(1) in names:
                bad.append((p.name, m.group(1)))
        for m in re.finditer(r"^\s*from\s+\.\s+import\s+(.+)$", t, re.M):
            bad += [(p.name, x.strip()) for x in m.group(1).split(",") if x.strip() in names]
        for m in re.finditer(r"(?:from|import)\s+(?:ops_feedback|\.\.)\.?components(?:\.|\s+import\s+)(\w+)", t):
            if m.group(1) in names:
                bad.append((p.name, m.group(1)))
    assert bad == [], bad


def test_3_http_only_in_link_components():
    bad = [p.relative_to(PKG).as_posix() for p in code_files() if HTTP_PY.search(p.read_text("utf-8")) and not p.name.startswith("link_") and not ENTRY.search(p.as_posix())]
    assert bad == [], bad


def test_3b_link_components_are_the_only_places_with_openit_link():
    bad = [p.relative_to(PKG).as_posix() for p in code_files() if "openit_link" in p.read_text("utf-8") and not p.name.startswith("link_")]
    assert bad == [], bad


def test_4_every_schema_has_passing_and_rejecting_examples():
    sys.path.insert(0, str(PKG.parent))
    from ops_feedback.wiring.validate import validator
    schemas = sorted(ROOT.joinpath("schemas").glob("*.schema.json"))
    assert schemas
    for s in schemas:
        name = s.name[: -len(".schema.json")]
        v = validator(name)
        valid = sorted((ROOT / "schemas" / "examples").glob(f"{name}.valid*.json"))
        invalid = sorted((ROOT / "schemas" / "examples").glob(f"{name}.invalid*.json"))
        assert valid and invalid, f"{name}: 통과·거부 예시가 모두 있어야 한다"
        for f in valid:
            errs = list(v.iter_errors(json.loads(f.read_text("utf-8"))))
            assert not errs, (f.name, errs[0].message[:120])
        for f in invalid:
            assert not v.is_valid(json.loads(f.read_text("utf-8"))), f"{f.name}은 거부되어야 한다"


def test_5_types_match_the_regenerated_output():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_types.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]


def test_6_every_code_file_belongs_somewhere():
    bad = []
    for p in code_files():
        rel = p.relative_to(PKG).as_posix()
        if not re.match(r"(components|wiring|types)/", rel) and not ENTRY.search(rel):
            bad.append(rel)
    assert bad == [], bad


def test_components_md_lists_every_component_file():
    t = (ROOT / "docs" / "components.md").read_text("utf-8")
    missing = [p.name for p in (PKG / "components").glob("*.py") if p.name != "__init__.py" and f"components/{p.name}" not in t]
    assert missing == [], missing


def test_schemas_readme_lists_out_of_schema_conditions():
    t = (ROOT / "schemas" / "README.md").read_text("utf-8")
    assert "스키마 밖 조건" in t

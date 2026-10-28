"""문진 스키마 복사본(src/ops_feedback/_schemas/survey/v1)이 원본(../survey-service/schema)과 같은지 해시로 확인한다.

원본은 읽기 전용 이웃이라 없는 환경(컨테이너 빌드, 새 clone)이 있다. 그때는 통과도 SKIP도 아닌 '확인 못 함'으로 구분해 낸다:
조용히 넘어가면 어긋남을 못 본 것을 본 것처럼 읽게 된다. 어긋남이 있으면 실패한다(고치는 법: scripts/sync_vendor.sh).
"""
import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ORIGIN = ROOT.parent / "survey-service" / "schema"
COPY = ROOT / "src" / "ops_feedback" / "_schemas" / "survey" / "v1"
# 복사 대상 -> 원본 상대 경로. scripts/sync_vendor.sh가 복사하는 목록과 같아야 한다(아래 시험이 확인).
PAIRS = {"TurnTrace.json": "schemas/TurnTrace.json", "CONTRACT_VERSION": "CONTRACT_VERSION"}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_sync_script_covers_every_vendored_file():
    script = (ROOT / "scripts" / "sync_vendor.sh").read_text(encoding="utf-8")
    assert {p.name for p in COPY.iterdir() if p.is_file()} == set(PAIRS)
    for name, rel in PAIRS.items():
        assert f'"$S"/{rel} ' in script and f"/v1/{name}" in script


@pytest.mark.parametrize("name", sorted(PAIRS))
def test_vendored_schema_matches_origin(name):
    origin = ORIGIN / PAIRS[name]
    if not origin.exists():
        # SKIP(그린처럼 보임) 대신 xfail로 '확인 못 함'을 사유와 함께 따로 집계되게 한다.
        pytest.xfail(f"확인 못 함: 원본 {origin}이 이 환경에 없다(복사본 {name}의 일치 여부를 확인하지 않았다)")
    assert _sha(COPY / name) == _sha(origin), f"{name}이 원본과 다르다. scripts/sync_vendor.sh로 동기화하라"

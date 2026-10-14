"""vendor로 복사한 스키마가 원본과 같은지(원본이 옆에 있을 때만)."""
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SNAP = ROOT / "src" / "ops_feedback" / "_schemas"
B = ROOT.parent / "boundary-contracts"
S = ROOT.parent / "survey-service" / "schema"


@pytest.mark.skipif(not B.exists(), reason="이웃 리포지토리가 없다")
def test_boundary_snapshot_current():
    for p in SNAP.joinpath("boundary").glob("*.json"):
        assert p.read_bytes() == (B / "schemas" / p.name).read_bytes(), f"{p.name} 스냅샷이 오래되었다: scripts/sync_vendor.sh"


@pytest.mark.skipif(not S.exists(), reason="이웃 리포지토리가 없다")
def test_survey_snapshot_current():
    assert (SNAP / "survey/v1/TurnTrace.json").read_bytes() == (S / "schemas/TurnTrace.json").read_bytes()
    assert (SNAP / "survey/v1/CONTRACT_VERSION").read_text().strip() == (S / "CONTRACT_VERSION").read_text().strip()

"""원본은 비식별 단계만 읽는다(D15): 소스 수준 점검. 읽기 권한(DeidentifierView)을 만드는 곳은 로그 저장소 컴포넌트, 건네는 곳은 wiring 두 파일뿐이다."""
import re
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "ops_feedback"
ALLOWED = {"components/raw_log_store.py", "wiring/log_store.py", "wiring/pipeline.py"}      # 저장소 구현, 저장소 조립(위임), 비식별 단계 조립


def test_only_log_store_and_pipeline_touch_raw_reading():
    offenders = []
    for p in SRC.rglob("*.py"):
        rel = p.relative_to(SRC).as_posix()
        if rel in ALLOWED:
            continue
        if re.search(r"read_unprocessed|grant_deidentifier_view|\._backend\b|raw_traces", p.read_text("utf-8")):
            offenders.append(rel)
    assert offenders == []


def test_pipeline_reads_raw_only_via_the_view_it_grants_once():
    t = (SRC / "wiring" / "pipeline.py").read_text("utf-8")
    assert "raw_traces" not in t and "_backend" not in t
    assert t.count("grant_deidentifier_view") == 1          # 읽기 권한을 건네는 유일한 지점(run_once)


def test_deidentifier_component_has_no_raw_access():
    t = (SRC / "components" / "deidentifier.py").read_text("utf-8")
    assert "raw_log_store" not in t and "read_unprocessed" not in t        # 봉투를 받을 뿐 원본을 읽어 오지 않는다


def test_failure_side_components_never_import_log_store():
    for name in ("failure_case_store.py", "failure_detector.py", "review_decider.py"):
        t = (SRC / "components" / name).read_text("utf-8")
        assert "log_store" not in t, name

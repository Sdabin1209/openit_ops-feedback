"""원본은 비식별 처리기만 읽는다(D15): 소스 수준 점검."""
import re
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "ops_feedback"


def test_only_deid_and_logstore_touch_raw_reading():
    offenders = []
    for p in SRC.glob("*.py"):
        text = p.read_text("utf-8")
        if p.name in ("logstore.py", "deid.py"):
            continue
        if re.search(r"read_unprocessed|grant_deidentifier_view|\._backend\b|raw_traces", text) and p.name != "job.py":
            offenders.append(p.name)
    assert offenders == []


def test_job_reads_raw_only_via_deidentifier():
    t = (SRC / "job.py").read_text("utf-8")
    assert "read_unprocessed" not in t and "raw_traces" not in t
    assert t.count("grant_deidentifier_view") == 1          # 조립 지점(build_from_config)에서 Deidentifier에 건네는 한 곳


def test_failure_side_modules_never_import_logstore_internals():
    for name in ("failurestore.py", "detect.py", "review.py"):
        t = (SRC / name).read_text("utf-8")
        assert "logstore" not in t, name

"""시험과 동작 코드에 실제 시간을 기다리거나 재는 곳이 없다(U422). 시계·타이머·대기는 주입받고(ADR 0027), 시험은 가짜를 넣는다.
허용: tests/waiting.py의 HANG_GUARD_SECONDS(매달림 감시 하나), 주입 자리의 기본값(`clock=time.monotonic` 같은 참조, 호출 아님)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = re.compile(r"time\.(monotonic|perf_counter|process_time|time|sleep)\(|(?<![A-Za-z_])timeout=\s*[\d.]+|\.settimeout\(\s*[\d.]+|\.wait\(\s*[\d.]+|\b(?:t|th|thread)\.join\(\s*[\d.]+|\.alarm\(|Timer\(\s*[\d.]+")
SRC = re.compile(r"time\.(monotonic|perf_counter|process_time|time|sleep)\(|threading\.Timer\(")


def test_tests_never_wait_on_or_measure_real_time():
    bad = []
    for p in sorted((ROOT / "tests").glob("*.py")):
        if p.name in ("waiting.py", "test_no_real_time.py"):
            continue
        for i, line in enumerate(p.read_text("utf-8").splitlines(), 1):
            if TESTS.search(line):
                bad.append(f"{p.name}:{i}: {line.strip()[:90]}")
    assert bad == [], bad


def test_code_never_reads_or_waits_on_the_clock_directly():
    """동작 코드는 시계·타이머를 인자나 클래스 속성으로 주입받는다(호출 형태 `time.monotonic()`·`threading.Timer(`가 없다)."""
    bad = []
    for p in sorted((ROOT / "src" / "ops_feedback").rglob("*.py")):
        for i, line in enumerate(p.read_text("utf-8").splitlines(), 1):
            if SRC.search(line) and not line.lstrip().startswith("#"):
                bad.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()[:90]}")
    assert bad == [], bad

"""잡 선언(deploy-jobs.json -> 이미지 라벨 io.openit.jobs)이 계약 JobsDeclaration을 지키고 실제 코드와 맞는다(U429, R7 생산자 검증).
이미지를 만들 때 build-image.sh가 같은 검증을 하고 어기면 이미지를 만들지 않는다(test_build_image.py가 라벨을 확인)."""
import json
import re
import subprocess
from pathlib import Path

from boundary_contracts import validator

ROOT = Path(__file__).resolve().parents[1]
DECL = json.loads((ROOT / "deploy-jobs.json").read_text(encoding="utf-8"))
SRC = ROOT / "src" / "ops_feedback"


def test_declaration_follows_the_contract():
    assert not list(validator("JobsDeclaration").iter_errors(DECL))


def test_declared_job_matches_the_code():
    (job,) = DECL["jobs"]
    assert job["argv"][:3] == ["python", "-m", "ops_feedback.job"] and (SRC / "job" / "__main__.py").exists()      # 선언한 명령의 모듈이 있다
    assert job["argv"][job["argv"].index("--config") + 1] == job["config"]["container_path"]                        # 설정 경로가 인자와 같다
    assert job["config"]["schema"] == "OpsFeedbackJobConfig.schema.json"                                           # 설정을 검증하는 계약 스키마(config.py)
    for name in job["secret_files"]:                                                                              # 선언한 비밀 파일을 코드가 실제로 읽는다(D84)
        base = name[: -len("_FILE")]
        assert re.search(rf'"{base}"|\b{base}\b', "".join(p.read_text("utf-8") for p in SRC.rglob("*.py") if "types" not in p.parts)), name


def test_a_declaration_that_breaks_the_contract_is_refused_by_the_build(tmp_path):
    """build-image.sh의 검증 부분만: 위반 선언이면 요청 검증 단계에서 종료 코드 2로 끝난다(이미지를 만들지 않는다)."""
    script = (ROOT / "scripts" / "build-image.sh").read_text("utf-8")
    assert 'validator("JobsDeclaration")' in script and "io.openit.jobs=$JOBS" in script
    bad = json.loads(json.dumps(DECL))
    bad["jobs"][0]["idempotent"] = False
    assert list(validator("JobsDeclaration").iter_errors(bad))

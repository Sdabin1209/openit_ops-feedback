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
    job = next(j for j in DECL["jobs"] if j["name"] == "ops-feedback-job")
    assert job["argv"][:3] == ["python", "-m", "ops_feedback.job"] and (SRC / "job" / "__main__.py").exists()      # 선언한 명령의 모듈이 있다
    assert job["argv"][job["argv"].index("--config") + 1] == job["config"]["container_path"]                        # 설정 경로가 인자와 같다
    assert job["config"]["schema"] == "OpsFeedbackJobConfig.schema.json"                                           # 설정을 검증하는 계약 스키마(config.py)
    for name in job["secret_files"]:                                                                              # 선언한 비밀 파일을 코드가 실제로 읽는다(D84)
        base = name[: -len("_FILE")]
        assert re.search(rf'"{base}"|\b{base}\b', "".join(p.read_text("utf-8") for p in SRC.rglob("*.py") if "types" not in p.parts) + (ROOT / "config" / "job.example.yaml").read_text("utf-8")), name      # OPS_GATE_*는 잡 설정의 {secret: 이름}이 읽는다


def test_a_declaration_that_breaks_the_contract_is_refused_by_the_build(tmp_path):
    """build-image.sh의 검증 부분만: 위반 선언이면 요청 검증 단계에서 종료 코드 2로 끝난다(이미지를 만들지 않는다)."""
    script = (ROOT / "scripts" / "build-image.sh").read_text("utf-8")
    assert 'validator("JobsDeclaration")' in script and "io.openit.jobs=$JOBS" in script
    bad = json.loads(json.dumps(DECL))
    bad["jobs"][0]["idempotent"] = False
    assert list(validator("JobsDeclaration").iter_errors(bad))


def test_every_job_has_a_timeout_and_migrate_is_declared():
    """R9.2·R9.3: 잡마다 timeout_seconds를 선언한다(없으면 3600초 기본이 되어 주기보다 길 수 있다). 스키마 변경 잡(C34 admin migrate)도 선언이다."""
    assert all(isinstance(j.get("timeout_seconds"), int) and j["timeout_seconds"] >= 1 for j in DECL["jobs"])
    m = next(j for j in DECL["jobs"] if j["name"] == "ops-feedback-migrate")
    assert m["argv"] == ["python", "-m", "ops_feedback.admin", "migrate"] and "needs" in m and "OPS_LOG_DATABASE_URL_FILE" in m["secret_files"]
    assert (SRC / "admin" / "__main__.py").exists()


def test_exposure_declares_egress_to_the_neighbours_this_area_calls_only():
    """CONVENTIONS 13절·R1.8: 영역이 부르는 상대(INTERFACES 한눈에: release-gate I4·I5)만 터널을 열어 달라고 선언한다. 정의에 없는 상대는 선언하지 않는다."""
    ex = json.loads((ROOT / "deploy-exposure.json").read_text("utf-8"))
    assert not list(validator("Exposure").iter_errors(ex))
    assert ex["egress"] == ["release-gate", "external:backup"]      # I4·I5 와 백업 저장소(R10.1)


def test_backup_job_is_declared_with_the_common_backup_names_and_a_timeout_matching_the_code():
    b = next(j for j in DECL["jobs"] if j["name"] == "ops-feedback-backup")
    assert b["argv"] == ["python", "-m", "ops_feedback.admin", "backup"] and b["env"] == ["OPENIT_BACKUP_URL"]
    assert {"OPENIT_BACKUP_CREDENTIALS_FILE", "OPENIT_BACKUP_REPO_PASSWORD_FILE"} <= set(b["secret_files"])
    from ops_feedback.wiring import pgbase
    assert b["timeout_seconds"] == pgbase.DUMP_STATEMENT_SECONDS      # 잡 상한과 덤프 문장 상한이 한 값(ADR 0032)


def test_exposure_body_limits_equal_the_servers_limits():
    """RUNTIME R3.5·R10.13(계약 3.4.7): 게이트웨이가 413 을 내는 경로별 상한(Exposure.max_body_bytes)이 서버의 실제 상한과 같다. 어긋나면 한쪽이 먼저 자른다(ADR 0034)."""
    import ops_feedback.components.link_ingest_api as api
    ex = json.loads((ROOT / "deploy-exposure.json").read_text("utf-8"))
    limit = {tuple(r["paths"]): r.get("max_body_bytes") for r in ex["routes"]}
    assert limit[("/v1/traces",)] == api.MAX_BODY and limit[("/v1/cases/*/review",)] == api.MAX_REVIEW_BODY

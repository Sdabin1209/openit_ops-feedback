"""영역 빌드 진입점(scripts/build-image.sh, 계약 BuildImageRequest/BuildImageResult, ADR 0022).
요청 거부 시험은 docker를 쓰지 않는다(DOCKER=/bin/false로 호출되면 실패하게 한다). 실제 빌드 시험은 docker 데몬이 있을 때만 돈다.
의존 소스 트리는 계약·문진 스키마 리포의 태그에서 git archive로 꺼낸다(리포가 없으면 건너뛴다). 위치는 WORKSPACE_DIR(기본: 이 리포의 상위)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from boundary_contracts import validator

import deps

ROOT = Path(__file__).resolve().parents[1]
BC_TAG, SS_TAG = deps.pinned_boundary_tag(), deps.pinned_survey_tag()      # 고정(requirements.txt)에서 읽는다: 올릴 때 시험 상수를 따로 고치지 않는다
SCRIPT = ROOT / "scripts" / "build-image.sh"


@pytest.fixture(scope="module")
def trees():
    return {"boundary": deps.boundary(), "survey_schema": deps.survey_schema()}


def request(trees, **over):
    req = {"source_dir": str(ROOT), "revision": "abcdef1", "versions": {"boundary": BC_TAG, "survey_schema": SS_TAG.removeprefix("survey-schema-")},
           "dependency_sources": {"boundary": str(trees["boundary"]), "survey_schema": str(trees["survey_schema"])},
           "image_tag": "ops-feedback-buildtest:t1"}
    req.update(over)
    return req


def run(tmp_path, req, docker="/bin/false"):
    p = tmp_path / "req.json"
    p.write_text(json.dumps(req))
    return subprocess.run(["bash", str(SCRIPT), str(p)], capture_output=True, text=True, env={**os.environ, "DOCKER": docker})


def mutate(trees, fn):
    req = request(trees)
    fn(req)
    return req


@pytest.mark.parametrize("name,fn,needle", [
    ("unknown key", lambda r: r.update(surprise=1), "BuildImageRequest"),
    ("missing revision", lambda r: r.pop("revision"), "BuildImageRequest"),
    ("bad revision", lambda r: r.update(revision="unknown"), "BuildImageRequest"),
    ("no survey_schema version", lambda r: r["versions"].pop("survey_schema"), "versions.survey_schema"),
    ("no survey_schema source", lambda r: r["dependency_sources"].pop("survey_schema"), "dependency_sources.survey_schema"),
    ("no boundary version", lambda r: r["versions"].pop("boundary"), "BuildImageRequest"),
    ("no boundary source", lambda r: r["dependency_sources"].pop("boundary"), "dependency_sources.boundary"),
    ("variant unknown to this area", lambda r: r.update(variant="gpu"), "variant"),
    ("wrong source_dir", lambda r: r.update(source_dir="/tmp"), "source_dir"),
    ("boundary tree is another version", lambda r: r["versions"].update(boundary="v2.4.1"), "패키지 버전"),
    ("survey tree is another version", lambda r: r["versions"].update(survey_schema="v1.2.0"), "패키지 버전"),
])
def test_bad_requests_fail_without_docker(trees, tmp_path, name, fn, needle):
    r = run(tmp_path, mutate(trees, fn))
    assert r.returncode == 2 and needle in r.stderr, (r.returncode, r.stderr[-300:])
    assert r.stdout == ""


def test_request_version_different_from_the_pinned_one_fails_without_a_label(trees, tmp_path):
    """U50: 라벨은 실제로 설치·사용한 버전을 말해야 한다. 요청이 이 리포의 고정 버전(requirements.txt)과 다르면 이미지를 만들지 않고 실패한다."""
    other = deps.boundary("v2.8.0")
    req = request(trees)
    req["versions"]["boundary"] = "v2.8.0"
    req["dependency_sources"]["boundary"] = str(other)           # 트리도 요청과 같은 버전이라 '트리 버전' 검사는 통과한다
    r = run(tmp_path, req)
    assert r.returncode == 2 and "고정 버전" in r.stderr and BC_TAG in r.stderr, r.stderr[-300:]
    assert r.stdout == ""


def test_survey_schema_request_different_from_the_pinned_one_fails(trees, tmp_path):
    other = deps.survey_schema("survey-schema-v1.3.0")
    req = request(trees)
    req["versions"]["survey_schema"] = "v1.3.0"
    req["dependency_sources"]["survey_schema"] = str(other)
    r = run(tmp_path, req)
    assert r.returncode == 2 and "고정 버전" in r.stderr and SS_TAG.removeprefix("survey-schema-") in r.stderr, r.stderr[-300:]


def test_unreadable_request_file_fails(tmp_path):
    p = tmp_path / "nope.json"
    r = subprocess.run(["bash", str(SCRIPT), str(p)], capture_output=True, text=True)
    assert r.returncode == 2


def _docker_ok() -> bool:
    return shutil.which("docker") is not None and subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _docker_ok(), reason="docker 데몬 없음")
def test_real_build_result_and_labels_follow_the_request_and_are_idempotent(trees, tmp_path):
    req = request(trees, revision="0123abc", image_tag="ops-feedback-buildtest:t42")
    try:
        r = run(tmp_path, req, docker="docker")
        assert r.returncode == 0, r.stderr[-800:]
        last = r.stdout.strip().splitlines()[-1]
        res = json.loads(last)
        assert not list(validator("BuildImageResult").iter_errors(res))
        assert res["image"] == "ops-feedback-buildtest:t42"
        lb = res["labels"]
        assert lb["io.openit.revision"] == "0123abc" and lb["io.openit.contracts.boundary"] == BC_TAG
        # 라벨은 이미지에 들어간 문진 스키마 패키지(openit-survey-schema) 버전의 주 번호다(2.0.0 -> "2"). 정수 CONTRACT_VERSION(모델 런타임 호환 번호)이 아니다
        assert lb["io.openit.contracts.survey-schema"] == SS_TAG.removeprefix("survey-schema-v").split(".")[0] and lb["io.openit.service"] == "ops-feedback"
        assert "io.openit.turn-engine" not in lb and lb["io.openit.model-runtime"] == "n/a"      # 3.0.0: turn-engine 라벨이 없어졌다
        exposure = json.loads(lb["io.openit.exposure"])                 # 노출 선언 라벨(U47): 리포의 deploy-exposure.json 그대로
        assert not list(validator("Exposure").iter_errors(exposure))
        assert exposure == json.loads((ROOT / "deploy-exposure.json").read_text(encoding="utf-8"))
        def layers():
            out = subprocess.run(["docker", "image", "inspect", "ops-feedback-buildtest:t42", "--format", "{{json .RootFS.Layers}}"], capture_output=True, text=True)
            return json.loads(out.stdout)
        first = layers()
        assert run(tmp_path, req, docker="docker").returncode == 0
        # 같은 요청 -> 같은 내용: 층(RootFS.Layers)이 같다. 이미지 ID는 BuildKit이 설정에 넣는 시각 때문에 매번 달라서 비교하지 않는다(ADR 0022).
        assert layers() == first
    finally:
        subprocess.run(["docker", "rmi", "-f", "ops-feedback-buildtest:t42"], capture_output=True)


@pytest.mark.skipif(not _docker_ok(), reason="docker 데몬 없음")
def test_survey_schema_label_is_the_package_major_not_the_integer_contract_version(trees, tmp_path):
    """U50(헤드 결정): 라벨 io.openit.contracts.survey-schema는 설치된 문진 스키마 패키지 버전의 주 번호다. 같은 트리에서 CONTRACT_VERSION만 7로 바꿔도
    라벨은 패키지 주 번호 그대로여야 한다(CONTRACT_VERSION에서 읽으면 "7"이 된다)."""
    tree = tmp_path / "ss-modified"
    shutil.copytree(trees["survey_schema"], tree)
    (tree / "CONTRACT_VERSION").write_text("7\n")
    req = request(trees, revision="0123abd", image_tag="ops-feedback-buildtest:t43")
    req["dependency_sources"]["survey_schema"] = str(tree)
    try:
        r = run(tmp_path, req, docker="docker")
        assert r.returncode == 0, r.stderr[-600:]
        label = json.loads(r.stdout.strip().splitlines()[-1])["labels"]["io.openit.contracts.survey-schema"]
        assert label == SS_TAG.removeprefix("survey-schema-v").split(".")[0] and label != "7"
    finally:
        subprocess.run(["docker", "rmi", "-f", "ops-feedback-buildtest:t43"], capture_output=True)

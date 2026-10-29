"""영역 빌드 진입점(scripts/build-image.sh, 계약 BuildImageRequest/BuildImageResult, ADR 0022).
요청 거부 시험은 docker를 쓰지 않는다(DOCKER=/bin/false로 호출되면 실패하게 한다). 실제 빌드 시험은 docker 데몬이 있을 때만 돈다.
의존 소스 트리는 계약·문진 스키마 리포의 태그에서 git archive로 꺼낸다(리포가 없으면 건너뛴다). 위치는 WORKSPACE_DIR(기본: 이 리포의 상위)."""
import json
import os
import shutil
import subprocess
import tarfile
import io
from pathlib import Path

import pytest
from boundary_contracts import validator

ROOT = Path(__file__).resolve().parents[1]
WS = Path(os.environ.get("WORKSPACE_DIR") or ROOT.parent)
BC_TAG, SS_TAG = "v3.0.0", "survey-schema-v1.1.0"
SCRIPT = ROOT / "scripts" / "build-image.sh"


def _extract(repo: str, tag: str, dest: Path, path: str | None = None) -> Path:
    cmd = ["git", "-C", str(WS / repo), "archive", tag] + ([path] if path else [])
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        pytest.skip(f"{repo} {tag}를 꺼내지 못했다(WORKSPACE_DIR={WS})")
    dest.mkdir(parents=True)
    tarfile.open(fileobj=io.BytesIO(r.stdout)).extractall(dest, filter="data")
    return dest / path if path else dest


@pytest.fixture(scope="module")
def trees(tmp_path_factory):
    base = tmp_path_factory.mktemp("deps")
    return {"boundary": _extract("boundary-contracts", BC_TAG, base / "bc"),
            "survey_schema": _extract("survey-service", SS_TAG, base / "ss", "schema")}


def request(trees, **over):
    req = {"source_dir": str(ROOT), "revision": "abcdef1", "versions": {"boundary": BC_TAG, "survey_schema": "v1.1.0"},
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


def _tree_of(repo, tag, dest, path=None):
    return _extract(repo, tag, dest, path)


def test_request_version_different_from_the_pinned_one_fails_without_a_label(trees, tmp_path):
    """U50: 라벨은 실제로 설치·사용한 버전을 말해야 한다. 요청이 이 리포의 고정 버전(requirements.txt)과 다르면 이미지를 만들지 않고 실패한다."""
    other = _tree_of("boundary-contracts", "v2.8.0", tmp_path / "bc280")
    req = request(trees)
    req["versions"]["boundary"] = "v2.8.0"
    req["dependency_sources"]["boundary"] = str(other)           # 트리도 요청과 같은 버전이라 '트리 버전' 검사는 통과한다
    r = run(tmp_path, req)
    assert r.returncode == 2 and "고정 버전" in r.stderr and "v3.0.0" in r.stderr, r.stderr[-300:]
    assert r.stdout == ""


def test_survey_schema_request_different_from_the_pinned_one_fails(trees, tmp_path):
    other = _tree_of("survey-service", "survey-schema-v1.3.0", tmp_path / "ss13", "schema")
    req = request(trees)
    req["versions"]["survey_schema"] = "v1.3.0"
    req["dependency_sources"]["survey_schema"] = str(other)
    r = run(tmp_path, req)
    assert r.returncode == 2 and "고정 버전" in r.stderr and "v1.1.0" in r.stderr, r.stderr[-300:]


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
        assert lb["io.openit.contracts.survey-schema"] == "1" and lb["io.openit.service"] == "ops-feedback"
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

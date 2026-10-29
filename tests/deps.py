"""시험이 쓰는 의존 소스 트리(계약, 문진 스키마)를 requirements.txt의 고정 태그에서 꺼낸다. 건너뛰지 않는다.
먼저 이웃 리포(WORKSPACE_DIR, 기본: 이 리포의 상위)의 git archive를 쓰고, 없으면 requirements.txt와 같은 GitHub 주소에서 그 태그만 얕게 받는다
(setup-env.sh가 이미 같은 접근을 요구한다). 둘 다 안 되면 시험이 실패한다: 이웃 리포 배치에 따라 시험이 조용히 빠지지 않게 한다(U50 재현에서 14건이 빠졌다)."""
import io
import os
import re
import subprocess
import tarfile
import tempfile
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WS = Path(os.environ.get("WORKSPACE_DIR") or ROOT.parent)
_REQ = (ROOT / "requirements.txt").read_text(encoding="utf-8")


def pinned_boundary_tag() -> str:
    return re.search(r"openit_boundary-contracts\.git@(v[0-9.]+)", _REQ).group(1)


def pinned_survey_tag() -> str:
    return re.search(r"openit_survey-service\.git@(survey-schema-v[0-9.]+)", _REQ).group(1)


@lru_cache(maxsize=None)
def tree(repo: str, tag: str, path: str = "") -> Path:
    """repo(boundary-contracts | survey-service)의 tag 시점 소스 트리(path가 있으면 그 하위 디렉터리)를 임시 폴더에 꺼내 경로를 돌려준다."""
    dest = Path(tempfile.mkdtemp(prefix=f"ops-deps-{repo}-"))
    local = WS / repo
    cmd = ["git", "-C", str(local), "archive", tag] + ([path] if path else [])
    r = subprocess.run(cmd, capture_output=True) if local.is_dir() else None
    if r is not None and r.returncode == 0:
        tarfile.open(fileobj=io.BytesIO(r.stdout)).extractall(dest, filter="data")
    else:
        url = f"git@github.com:Sdabin1209/openit_{repo}.git"
        c = subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", tag, url, str(dest / "clone")], capture_output=True, text=True)
        if c.returncode != 0:
            raise RuntimeError(f"{repo} {tag}를 이웃 리포({local})에서도 {url}에서도 꺼내지 못했다: {c.stderr.strip()[-200:]}")
        tarball = subprocess.run(["git", "-C", str(dest / "clone"), "archive", "HEAD"] + ([path] if path else []), capture_output=True, check=True).stdout
        out = dest / "tree"
        out.mkdir()
        tarfile.open(fileobj=io.BytesIO(tarball)).extractall(out, filter="data")
        dest = out
    return dest / path if path else dest


def boundary(tag: str | None = None) -> Path:
    return tree("boundary-contracts", tag or pinned_boundary_tag())


def survey_schema(tag: str | None = None) -> Path:
    return tree("survey-service", tag or pinned_survey_tag(), "schema")


def conformance() -> Path:
    """계약 conformance 폴더. OPENIT_CONFORMANCE_DIR이 있으면 그것, 없으면 고정 태그의 소스 트리 안의 것."""
    env = os.environ.get("OPENIT_CONFORMANCE_DIR")
    return Path(env) if env and (Path(env) / "examples_invalid").is_dir() else boundary() / "conformance"

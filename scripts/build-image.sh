#!/usr/bin/env bash
# 영역 빌드 진입점(계약 BuildImageRequest/BuildImageResult 2.5.0, OPEN_GAPS U42, docs/adr/0022). 사용: bash scripts/build-image.sh <요청 JSON 파일>
# 요청은 계약 validator("BuildImageRequest")로 검증한다. 이웃 리포 경로나 빌드 중 네트워크 대신 요청의 dependency_sources(해당 버전 소스 트리)만 쓴다.
# 진행 기록은 표준 오류, 표준 출력 마지막 줄이 BuildImageResult JSON 한 줄. 종료 0 성공. DOCKER 환경 변수(없으면 docker)만 받는다. 비밀은 다루지 않는다.
set -euo pipefail
REQ=${1:?사용법: bash scripts/build-image.sh <요청 JSON 파일>}
DOCKER=${DOCKER:-docker}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# 왜 이 파이썬: 요청 검증에 계약 패키지가 필요하다. 영역 가상환경이 있으면 그것을, 없으면 PYTHON(또는 python3)을 쓴다.
PY=${PYTHON:-$([ -x "$HERE/.venv/bin/python" ] && echo "$HERE/.venv/bin/python" || echo python3)}
log() { echo "build-image: $*" >&2; }

# 1) 요청 검증과 값 꺼내기. 계약 패키지는 요청이 가리키는 boundary 소스 트리에서 읽는다(설치본에 기대지 않는다).
eval "$("$PY" - "$REQ" "$HERE" <<'PYEOF'
import json, os, re, shlex, sys
req_path, here = sys.argv[1], sys.argv[2]
def die(msg):
    print(f"echo {shlex.quote('build-image: 요청 오류: ' + msg)} >&2; exit 2")
    sys.exit(0)
try:
    req = json.load(open(req_path, encoding="utf-8"))
except (OSError, ValueError) as e:
    die(f"요청 파일을 읽지 못했다({type(e).__name__})")
ds = req.get("dependency_sources") if isinstance(req, dict) else None
bsrc = ds.get("boundary") if isinstance(ds, dict) else None
if not bsrc or not os.path.isdir(bsrc):
    die("dependency_sources.boundary가 필요하다(절대 경로의 boundary-contracts 소스 트리)")
sys.path.insert(0, bsrc)
from boundary_contracts import validator
errs = sorted(validator("BuildImageRequest").iter_errors(req), key=lambda e: list(map(str, e.absolute_path)))
if errs:
    die("BuildImageRequest 위반: " + "; ".join(f"{'.'.join(map(str, e.absolute_path)) or '<root>'}: {e.validator}" for e in errs[:5]))
v, ssrc = req["versions"], ds.get("survey_schema")
# 이 영역이 쓰는 키: versions.boundary, versions.survey_schema, dependency_sources.boundary, dependency_sources.survey_schema. 없으면 기본값을 두지 않고 실패한다.
if "survey_schema" not in v or not ssrc or not os.path.isdir(ssrc):
    die("versions.survey_schema와 dependency_sources.survey_schema(문진 스키마 패키지 디렉터리)가 필요하다")
if "variant" in req:
    die(f"이 영역은 variant를 모른다: {req['variant']}")
if os.path.realpath(req["source_dir"]) != os.path.realpath(here):
    die("source_dir가 이 리포 루트가 아니다")
def pkg_version(tree):
    m = re.search(r'^version\s*=\s*"([^"]+)"', open(os.path.join(tree, "pyproject.toml"), encoding="utf-8").read(), re.M)
    return m.group(1) if m else None
# 소스 트리가 요청한 버전의 것인지 확인한다(기준표와 어긋난 트리로 라벨만 맞춰 찍지 않는다).
for name, tree, want in (("boundary", bsrc, v["boundary"]), ("survey_schema", ssrc, v["survey_schema"])):
    got = pkg_version(tree)
    if got != want.lstrip("v"):
        die(f"dependency_sources.{name}의 패키지 버전({got})이 versions.{name}({want})과 다르다")
sv = open(os.path.join(ssrc, "CONTRACT_VERSION"), encoding="utf-8").read().strip()      # 라벨 io.openit.contracts.survey-schema는 문진 스키마의 정수 계약 버전
# 노출 선언(라벨 io.openit.exposure): 리포의 deploy-exposure.json이 정본이다. 계약 Exposure로 검증한 뒤 한 줄 JSON으로 라벨에 단다.
try:
    exposure = json.load(open(os.path.join(here, "deploy-exposure.json"), encoding="utf-8"))
except (OSError, ValueError) as e:
    die(f"deploy-exposure.json을 읽지 못했다({type(e).__name__})")
eerrs = [e for e in validator("Exposure").iter_errors(exposure)]
if eerrs:
    die("deploy-exposure.json이 Exposure를 어긴다: " + "; ".join(f"{'.'.join(map(str, e.absolute_path)) or '<root>'}: {e.validator}" for e in eerrs[:3]))
for k, val in dict(EXPOSURE=json.dumps(exposure, ensure_ascii=False, separators=(",", ":"), sort_keys=True), SRC_B=bsrc, SRC_S=ssrc, REV=req["revision"], BC=v["boundary"], SS_INT=sv, TAG=req["image_tag"]).items():
    print(f"{k}={shlex.quote(val)}")
PYEOF
)"

# 2) wheel: 소스 트리를 복사해(요청이 준 트리에 빌드 부산물을 남기지 않는다) 만든다. 같은 요청이 같은 wheel이 되도록 시각을 고정한다.
cd "$HERE"
rm -rf build/wheels build/src && mkdir -p build/wheels build/src
cp -r "$SRC_B" build/src/boundary && cp -r "$SRC_S" build/src/survey-schema
export SOURCE_DATE_EPOCH=315532800        # 근거: wheel이 파일 시각을 이 값으로 고정하는 표준 환경 변수(reproducible-builds). 값은 임의(1980-01-01).
for t in boundary survey-schema; do
  log "wheel: $t"
  "$PY" -m pip wheel -q --no-deps --no-build-isolation -w build/wheels "build/src/$t" >&2
done

# 3) 이미지. 인자 이름은 이 영역의 Dockerfile이 정한다(여기에만 있다). turn-engine·model-runtime은 이 이미지에 들어 있지 않아 n/a다.
log "docker build $TAG"
"$DOCKER" build -q -t "$TAG" --build-arg COMMIT="$REV" --build-arg CONTRACTS_VERSION="$BC" --build-arg SURVEY_SCHEMA_VERSION="$SS_INT" \
  --build-arg TURN_ENGINE_VERSION=n/a --build-arg MODEL_RUNTIME_VERSION=n/a --label "io.openit.exposure=$EXPOSURE" "$HERE" >&2

# 4) 결과: 이미지에 실제로 붙은 라벨을 docker inspect로 읽고 BuildImageResult로 검증한 한 줄을 마지막에 낸다.
IID=$("$DOCKER" image inspect "$TAG" --format '{{.Id}}')
LABELS=$("$DOCKER" image inspect "$TAG" --format '{{json .Config.Labels}}')
"$PY" - "$SRC_B" "$TAG" "$IID" "$LABELS" <<'PYEOF'
import json, sys
sys.path.insert(0, sys.argv[1])
from boundary_contracts import validator
tag, iid, labels = sys.argv[2], sys.argv[3], {k: v for k, v in json.loads(sys.argv[4]).items() if k.startswith("io.openit.")}
res = {"image": tag, "image_id": iid, "labels": labels}
errs = [e.message[:120] for e in validator("BuildImageResult").iter_errors(res)]
if errs:
    sys.exit("build-image: 결과가 BuildImageResult를 어긴다: " + "; ".join(errs[:3]))
print(json.dumps(res, ensure_ascii=False, sort_keys=True))
PYEOF

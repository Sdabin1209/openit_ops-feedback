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
# U50(라벨 정직성): 라벨은 요청이 아니라 실제로 설치·사용하는 버전을 말해야 한다. 이 리포의 고정 버전(requirements.txt)과 요청이 다르면 라벨을 찍지 않고 실패한다.
reqs = open(os.path.join(here, "requirements.txt"), encoding="utf-8").read()
pin_b = re.search(r"openit_boundary-contracts\.git@(v[0-9.]+)", reqs)
pin_s = re.search(r"openit_survey-service\.git@survey-schema-(v[0-9.]+)", reqs)
if not pin_b or not pin_s:
    die("requirements.txt에서 고정 버전(boundary-contracts, 문진 스키마)을 읽지 못했다")
for name, want, pin in (("boundary", v["boundary"], pin_b.group(1)), ("survey_schema", v["survey_schema"], pin_s.group(1))):
    if want != pin:
        die(f"요청 versions.{name}({want})가 이 리포의 고정 버전({pin}, requirements.txt)과 다르다. 이미지는 고정 버전을 쓰므로 라벨을 찍지 않고 실패한다. 고정을 올리는 것은 영역의 일이다")
# 노출 선언(라벨 io.openit.exposure): 리포의 deploy-exposure.json이 정본이다. 계약 Exposure로 검증한 뒤 한 줄 JSON으로 라벨에 단다.
try:
    exposure = json.load(open(os.path.join(here, "deploy-exposure.json"), encoding="utf-8"))
except (OSError, ValueError) as e:
    die(f"deploy-exposure.json을 읽지 못했다({type(e).__name__})")
eerrs = [e for e in validator("Exposure").iter_errors(exposure)]
if eerrs:
    die("deploy-exposure.json이 Exposure를 어긴다: " + "; ".join(f"{'.'.join(map(str, e.absolute_path)) or '<root>'}: {e.validator}" for e in eerrs[:3]))
# 잡 선언(라벨 io.openit.jobs): 리포의 deploy-jobs.json이 정본이다. 생산자 검증(R7): 계약 JobsDeclaration으로 검증한 뒤 한 줄 JSON으로 라벨에 단다. 어기면 이미지를 만들지 않는다.
try:
    jobs = json.load(open(os.path.join(here, "deploy-jobs.json"), encoding="utf-8"))
except (OSError, ValueError) as e:
    die(f"deploy-jobs.json을 읽지 못했다({type(e).__name__})")
jerrs = [e for e in validator("JobsDeclaration").iter_errors(jobs)]
if jerrs:
    die("deploy-jobs.json이 JobsDeclaration을 어긴다: " + "; ".join(f"{'.'.join(map(str, e.absolute_path)) or '<root>'}: {e.validator}" for e in jerrs[:3]))
for k, val in dict(EXPOSURE=json.dumps(exposure, ensure_ascii=False, separators=(",", ":"), sort_keys=True), JOBS=json.dumps(jobs, ensure_ascii=False, separators=(",", ":"), sort_keys=True), SRC_B=bsrc, SRC_S=ssrc, REV=req["revision"], BC=v["boundary"], TAG=req["image_tag"]).items():
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

# 통신 기반 openit-link(D118)는 BuildImageRequest의 dependency_sources에 자리가 없어(계약이 모른다) requirements.txt의 고정 태그로 직접 wheel을 만든다.
# 왜 호스트에서 만드나: docker 안에는 ssh 키가 없다(위의 wheel들과 같은 이유). 의존(boundary-contracts 등)은 위에서 만든 wheel과 requirements.lock이 채우므로 --no-deps.
LINK_REQ=$(sed -n 's/^openit-link @ //p' requirements.txt | head -1)
[ -n "$LINK_REQ" ] || { log "requirements.txt에서 openit-link 고정을 읽지 못했다"; exit 2; }
log "wheel: openit-link ($LINK_REQ)"
"$PY" -m pip wheel -q --no-deps --no-build-isolation -w build/wheels "$LINK_REQ" >&2 || "$PY" -m pip wheel -q --no-deps -w build/wheels "$LINK_REQ" >&2

# 라벨 값은 방금 만든 wheel의 실제 버전에서 읽는다(요청 값을 그대로 찍지 않는다). 위 검사로 요청·고정·소스 트리는 이미 같다.
BC=$("$PY" - <<'PYEOF'
import glob, re, sys
w = glob.glob("build/wheels/boundary_contracts-*.whl")
m = re.match(r"boundary_contracts-([0-9]+\.[0-9]+\.[0-9]+)-", w[0].split("/")[-1]) if len(w) == 1 else None
sys.exit("build-image: boundary-contracts wheel의 버전을 읽지 못했다") if not m else print("v" + m.group(1))
PYEOF
)
# 라벨 io.openit.contracts.survey-schema는 문진 스키마 패키지(openit-survey-schema)의 주 번호다(3.0.0 -> 3). 정수 CONTRACT_VERSION(모델 런타임 호환 번호)이 아니다.
SS_INT=$("$PY" - <<'PYEOF'
import glob, re, sys
w = glob.glob("build/wheels/openit_survey_schema-*.whl")
m = re.match(r"openit_survey_schema-([0-9]+)\.[0-9]+\.[0-9]+-", w[0].split("/")[-1]) if len(w) == 1 else None
sys.exit("build-image: 문진 스키마 wheel의 버전을 읽지 못했다") if not m else print(m.group(1))
PYEOF
)
# 3) 이미지. 인자 이름은 이 영역의 Dockerfile이 정한다(여기에만 있다). model-runtime은 이 이미지에 들어 있지 않아 n/a다(turn-engine 라벨은 3.0.0에서 없어졌다).
log "docker build $TAG"
"$DOCKER" build -q -t "$TAG" --build-arg COMMIT="$REV" --build-arg CONTRACTS_VERSION="$BC" --build-arg SURVEY_SCHEMA_VERSION="$SS_INT" \
  --build-arg MODEL_RUNTIME_VERSION=n/a --label "io.openit.exposure=$EXPOSURE" --label "io.openit.jobs=$JOBS" "$HERE" >&2

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

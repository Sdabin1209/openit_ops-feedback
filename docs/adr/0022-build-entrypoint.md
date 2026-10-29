# 0022. 영역 빌드 진입점 scripts/build-image.sh

- 상태: 채택(헤드 결정 U42, 계약 boundary-contracts 2.5.0의 BuildImageRequest/BuildImageResult).
- 맥락: deploy의 `build-images.sh`가 이 영역의 Dockerfile 인자 이름(COMMIT, CONTRACTS_VERSION, SURVEY_SCHEMA_VERSION ...)과 빌드 산출 파일(`build/wheels/BOUNDARY_TAG`, `SURVEY_SCHEMA_VERSION`)을 직접 알았다. 영역이 서로(deploy와)의 구조를 모른다는 구조 규칙 1에 어긋났다(U23 감사, OPEN_GAPS U42).
- 결정: 영역 리포 루트의 `bash scripts/build-image.sh <요청 JSON>`이 유일한 빌드 진입점이다. 요청은 계약 `BuildImageRequest`로 검증하고(계약 패키지는 요청이 가리키는 boundary 소스 트리에서 읽는다), 결과는 표준 출력 마지막 줄에 `BuildImageResult` 한 줄(image, image_id, 이미지에 실제로 붙은 `io.openit.*` 라벨)로 낸다. 진행 기록은 표준 오류.
- 이 영역의 규칙(계약이 영역에 맡긴 것):
  - 쓰는 키: `versions.boundary`, `versions.survey_schema`, `dependency_sources.boundary`, `dependency_sources.survey_schema`. 없으면 기본값 없이 실패한다(종료 2).
  - `variant`는 모른다(이미지가 하나다): 주면 실패.
  - 소스 트리의 패키지 버전(`pyproject.toml`)이 요청한 버전과 다르면 실패한다. 기준표와 어긋난 트리에 라벨만 맞춰 찍는 것을 막는다.
  - turn-engine·model-runtime은 이 이미지에 들어 있지 않아 라벨이 `n/a`다(요청의 `turn_engine`은 폐기라 무시).
  - 라벨 `contracts.survey-schema`는 문진 스키마의 정수 계약 버전(`CONTRACT_VERSION`)이다(`ImageLabels`가 정수 또는 `n/a`만 허용).
  - 이웃 리포 경로와 빌드 중 네트워크(SSH)는 쓰지 않는다. wheel은 `dependency_sources` 트리를 `build/src`에 복사해 만든다(요청이 준 트리에 빌드 부산물을 남기지 않는다).
- 왜: 인자 이름·산출 파일은 영역이 정하고 deploy는 계약 모양의 요청과 결과로만 만난다(구조 규칙 1, 6). 요청 검증을 계약 validator로 하면 손으로 같은 검사를 되풀이하지 않는다(R7).
- 같은 요청 → 같은 이미지: wheel을 `SOURCE_DATE_EPOCH=315532800`(reproducible-builds의 표준 환경 변수, 값은 임의)로 만들어 같은 요청이 같은 wheel이 되는 것을 확인했다(해시 비교). 이미지 층(`RootFS.Layers`)은 같다. **이미지 ID는 같지 않다**: BuildKit이 설정에 시각을 넣어 같은 입력에도 매번 달라진다(실측). 시험은 층이 같음을 확인한다.
- 이전 빌드와 같은지: 이전 deploy 블록은 `build_wheels.sh`(git archive + pip wheel) 뒤 `docker build`에 COMMIT, CONTRACTS_VERSION, SURVEY_SCHEMA_VERSION, TURN_ENGINE_VERSION=n/a, MODEL_RUNTIME_VERSION=n/a를 넘겼다. 같은 Dockerfile과 같은 인자를 넘기므로 이미지 내용과 라벨은 같다(라벨 값 비교 외에 이전 이미지와의 층 비교는 하지 않았다).
- 옛 `scripts/build_wheels.sh`: 옛 deploy 스크립트가 아직 부르므로 남겨 두고 폐기 예정으로 표시했다. deploy가 새 진입점으로 바꾼 뒤 지운다.
- wheel 빌드 방식: `pip wheel --no-deps --no-build-isolation`이다. 격리 빌드(PEP 517 기본)는 빌드 때마다 setuptools를 PyPI에서 받아야 해서, 계약이 빌드 중 네트워크를 쓰지 말라고 한 것과 어긋나고 같은 요청이 때에 따라 다른 빌드 도구로 만들어질 수 있다. 그래서 격리하지 않고 빌드 의존(`setuptools==84.2.1`, `wheel==0.48.0`)을 `requirements.txt`에 고정해 `scripts/setup-env.sh`가 설치한다(이미지용 `requirements.lock`에는 넣지 않는다). 깨끗한 clone에서 setup-env 없이 부르면 setuptools가 없어 실패했다(헤드 재현): 우연히 설치돼 있던 venv에서만 통과한 것이었다.
- 검토한 대안: Dockerfile 안에서 이웃 태그를 받기(빌드 중 SSH 필요, 계약이 금지), 요청 검증을 bash로 직접 쓰기(손코드 중복).
- 결과: 얻는 것은 deploy가 영역 내부(Dockerfile 인자, 산출 파일)를 몰라도 되는 것. 감수하는 것은 요청 검증에 쓰는 파이썬이 필요한 것(영역 `.venv` 또는 `PYTHON`, `jsonschema` 필요)과 위 이미지 ID 비재현.
- 되돌리는 조건: 계약이 BuildImageRequest를 바꾸면 이 진입점을 따라 바꾼다.

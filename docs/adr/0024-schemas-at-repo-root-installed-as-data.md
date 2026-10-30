# 0024. 영역 스키마는 리포 루트 `schemas/`에 두고 설치본에는 data-files로 싣는다

- 상태: 채택(U400, 헤드 DESIGN.md 3절 "`schemas/` (리포 루트)").
- 맥락: 영역 내부 타입의 JSON Schema를 리포 루트 `schemas/`에 두라고 정해졌다. 컴포넌트는 런타임에 이 파일로 입력을 검증하므로 패키지 밖 파일을 이미지에서도 찾아야 한다.
- 결정: 스키마 파일은 `schemas/*.schema.json`(예시는 `schemas/examples/`). `pyproject.toml`의 `[tool.setuptools.data-files]`로 `<prefix>/share/ops-feedback/schemas`에 설치한다. 찾는 순서는 소스 트리의 `schemas/`, 그다음 `sys.prefix/share/ops-feedback/schemas`(`wiring/validate.py`). Dockerfile은 `COPY schemas /src/schemas`로 설치 입력에 넣는다.
- 왜: 스키마를 패키지 안으로 옮기면 DESIGN.md가 정한 위치(리포 루트)와 다르다. 복사본 두 벌은 어긋난다(D110이 문진 스키마 사본을 금지한 이유와 같다). data-files는 setuptools의 표준 기능이고 `sys.prefix`는 가상환경과 이미지 모두에서 설치 위치를 가리킨다. 환경 변수로 위치를 받는 방법은 설정 키가 하나 늘고 빠뜨리면 조용히 다른 곳을 본다.
- 검토한 대안: `package-data`로 `src/ops_feedback/schemas/`에 복사(위치 규칙 위반, 사본), 심볼릭 링크(wheel 빌드가 링크를 따라가는지 도구마다 달라 확인하지 못했다), 환경 변수 `OPS_SCHEMA_DIR`(위).
- 결과: 얻는 것은 스키마 한 벌과 표준 설치. 감수하는 것은 setuptools data-files가 "deprecated 방향"의 기능이라는 점(2026-10-30 `pip install`로 `share/ops-feedback/schemas/`가 생기는 것을 확인했다. 이미지에서의 확인은 `scripts/smoke.sh` 결과를 본다).
- 되돌리는 조건: 설치 도구가 data-files를 지원하지 않게 되면 `importlib.resources`로 읽는 패키지 데이터로 옮기되, 그때 `schemas/`와의 관계(생성 복사 등)를 새 ADR로 정한다.

"""시험이 하위 프로세스에 넘기는 환경의 한 곳(RUNTIME R9.1: 상속받은 환경 변수를 통째로 쓰거나 넘기지 않는다).
허용 목록은 head check-container-inputs 와 같은 최소 집합이다. 호출이 더 필요한 이름은 이름으로 더한다(`names`), 값을 새로 정하는 것은 `extra`다.
docker 설정(DOCKER_CONFIG·DOCKER_HOST)은 tests/conftest.py 가 scripts/docker-env.sh 규칙으로 os.environ 에 넣어 두므로 여기서 허용 목록으로 옮겨진다."""
from __future__ import annotations

import os

# 왜 Docker 연결 이름까지: 컨테이너를 부르는 시험이 소켓·컨텍스트·TLS·ssh 연결을 잃지 않게 한다(codex C2 검토, 2026-11-09).
ALLOWED = ("PATH", "HOME", "LANG", "TMPDIR", "DOCKER_CONFIG", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "SSH_AUTH_SOCK")


def clean_env(extra: dict | None = None, *names: str) -> dict:
    env = {k: os.environ[k] for k in (*ALLOWED, *names) if k in os.environ}
    env.update(extra or {})
    return env

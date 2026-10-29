#!/usr/bin/env bash
# 왜: 영역 환경 준비의 정본은 이 파일이다. 새 머신으로 옮길 때 README·런북마다 설치법이 달라(이웃 폴더 editable 등) 혼자 설치되지 않았다. 한 곳으로 모았다.
# 버전은 requirements.lock(서드파티)과 requirements.txt(계약 Git 태그)가 정한다. 이 스크립트에는 버전·태그를 적지 않는다.
# 규약(계약 SetupEnvReport 2.6.0, OPEN_GAPS U43): 인자 없음, 여러 번 실행해도 같은 환경이 된다(멱등), 비밀을 다루지 않는다.
# 진행 기록(pip 출력 포함)은 표준 오류로 내고, 표준 출력 마지막 줄은 SetupEnvReport JSON 한 줄이다(실패해도 낸다). deploy는 그 줄만 읽는다.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
AREA=ops-feedback
VENV=.venv
TEST_COMMAND='[".venv/bin/python", "-m", "pytest", "-q"]'     # 리포 루트에서 영역 시험 전체를 돌리는 argv

report() {   # report <status> <exit_code>
  python3 - "$AREA" "$1" "$2" "$SECONDS" "$VENV" "$TEST_COMMAND" <<'PYEOF'
import json, sys
area, status, code, secs, venv, cmd = sys.argv[1:]
print(json.dumps({"area": area, "status": status, "exit_code": int(code), "seconds": int(secs),
                  "environment": {"kind": "python-venv", "path": venv, "test_command": json.loads(cmd)}}, ensure_ascii=False))
PYEOF
}

fail() {
  echo "setup-env: $1" >&2
  report fail 1
  exit 1
}

[ -d "$VENV" ] || python3 -m venv "$VENV" >&2 || fail "가상환경을 만들지 못했다(Python 3.10 이상, venv 모듈 확인)"
PIP="$VENV/bin/pip"

install_all() {
  # 왜: 오래된 설치(예: 이웃 폴더 editable)가 남아 있어도 핀과 같아지게, 계약과 이 패키지는 지우고 다시 깐다.
  "$PIP" uninstall -y boundary-contracts openit-survey-schema ops-feedback >&2 || true
  "$PIP" install -r requirements.lock -r requirements.txt -e '.[dev]' >&2
}

if ! install_all; then
  echo "setup-env: 설치 실패, ${SETUP_ENV_RETRY_WAIT:-10}초 뒤 한 번 재시도한다(네트워크 일시 오류 대비)." >&2
  sleep "${SETUP_ENV_RETRY_WAIT:-10}"
  install_all || fail "실패. pip 설치가 두 번 모두 실패했다. 네트워크, requirements.txt의 git+ssh 계약 태그 접근(SSH 키/GitHub 권한), Python 3.10 이상 여부를 확인하라."
fi

echo "setup-env: 완료. 시험: .venv/bin/python -m pytest -q" >&2
report ok 0

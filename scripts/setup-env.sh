#!/usr/bin/env bash
# 왜: 영역 환경 준비의 정본은 이 파일이다. 새 머신으로 옮길 때 README·런북마다 설치법이 달라(이웃 폴더 editable 등) 혼자 설치되지 않았다. 한 곳으로 모았다.
# 버전은 requirements.lock(서드파티)과 requirements.txt(계약 Git 태그)가 정한다. 이 스크립트에는 버전·태그를 적지 않는다.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

[ -d .venv ] || python3 -m venv .venv
PIP=.venv/bin/pip

install_all() {
  # 왜: 오래된 설치(예: 이웃 폴더 editable)가 남아 있어도 핀과 같아지게, 계약과 이 패키지는 지우고 다시 깐다.
  "$PIP" uninstall -y boundary-contracts openit-survey-schema ops-feedback >/dev/null 2>&1 || true
  "$PIP" install -r requirements.lock -r requirements.txt -e '.[dev]'
}

if ! install_all; then
  echo "setup-env: 설치 실패, 10초 뒤 한 번 재시도한다(네트워크 일시 오류 대비)." >&2
  sleep 10
  if ! install_all; then
    echo "setup-env: 실패. pip 설치가 두 번 모두 실패했다. 네트워크, requirements.txt의 git+ssh 계약 태그 접근(SSH 키/GitHub 권한), Python 3.10 이상 여부를 확인하라." >&2
    exit 1
  fi
fi

echo "setup-env: 완료. 시험: .venv/bin/python -m pytest -q"

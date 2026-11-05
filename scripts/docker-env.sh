# 리포의 docker 호출이 사용자 전역 ~/.docker 설정에 기대지 않게 한다(Docker 공식 DOCKER_CONFIG). 사용법: `. scripts/docker-env.sh` (스크립트) / tests/conftest.py 가 한 번 읽어 환경에 넣는다. 이 파일이 규칙의 한 곳이다.
#  - DOCKER_CONFIG 가 주어지면 그대로 쓴다.
#  - 없으면 리포 build/docker-config 의 빈 설정({})을 쓴다. 사용자 ~/.docker 를 이 파일이 읽거나 고치지 않는다.
#  - 현재 컨텍스트의 엔드포인트가 기본 소켓이 아니면(원격·rootless 등) 그 주소를 DOCKER_HOST 로 넘긴다. 엔드포인트는 docker CLI 에 묻는다(설정 파일을 직접 읽지 않는다). DOCKER_HOST 가 이미 있으면 그대로.
_docker_env_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [ -z "${DOCKER_CONFIG:-}" ]; then
  if [ -z "${DOCKER_HOST:-}" ]; then
    _h=$(${DOCKER:-docker} context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null || true)
    case "$_h" in ""|unix:///var/run/docker.sock) ;; *) export DOCKER_HOST="$_h" ;; esac
  fi
  mkdir -p "$_docker_env_root/build/docker-config"
  [ -f "$_docker_env_root/build/docker-config/config.json" ] || echo '{}' > "$_docker_env_root/build/docker-config/config.json"
  export DOCKER_CONFIG="$_docker_env_root/build/docker-config"
fi
unset _docker_env_root _h

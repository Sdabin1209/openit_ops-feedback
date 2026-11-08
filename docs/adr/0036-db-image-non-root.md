# 0036. ops-feedback-db 는 root 가 아닌 postgres(uid 999)로 실행한다

- 상태: 채택(대장 #8). [0035](0035-area-database-image.md)의 실행 사용자·capability·root 단계 부분을 대체한다(나머지는 0035 그대로).
- 맥락: 0035 의 이미지는 uid 0 으로 시작해 진입점이 인증서·비밀 사본을 postgres 소유로 만들고 공식 진입점의 gosu 로 내려갔다. 그래서 선언(`db/inputs.json`)에 uid 0 과 capability 5개(CHOWN·DAC_OVERRIDE·FOWNER·SETUID·SETGID)가 필요했다. DB 컨테이너가 침해되면 root 와 그 capability 가 같이 넘어간다.
- 결정: Dockerfile 에 `USER postgres`(이 이미지에서 uid 999, gid 999). 진입점과 인증서 감시는 `install -o/-g`·`gosu`를 쓰지 않고 자기 사용자로 사본을 만든다. `inputs.json`: process 999/999, 쓰기 경로(`/run`, `/tmp`, 데이터 폴더)의 written_by 999/999, capabilities `[]`. 비밀 파일 읽기는 0640·그룹 SECRETS_GID(시작 프로세스의 보조 그룹)로 하고(계약 ContainerInputs 의 opened_by 설명), 사용자를 바꾸지 않으므로 보조 그룹이 유지된다.
- 왜: (1) 공식 postgres 이미지가 `--user` 로 root 없이 뜨도록 만들어져 있다(공식 문서 "Arbitrary --user Notes": 데이터 폴더가 그 사용자 쓰기 가능이어야 한다). (2) 최소 권한: 더하는 capability 가 없다. (3) 계약 ContainerInputs 가 "이미지 USER 의 숫자"를 process 로 선언하게 하고 배치 영역이 그 사용자로 쓰기 경로를 준다. `scripts/build-image.sh` 가 선언의 uid/gid 와 이미지 USER 를 대조한다.
- 검토한 대안: root 유지(위 이유로 버림). 시작은 root, 서버만 gosu 로 내림(공식 기본: root 단계가 남고 capability 5개가 필요해 버림). `--user` 를 배치가 덮어씀(계약이 배치의 user 덮어쓰기를 금지: "배치 영역은 user 를 덮어쓰지 않는다").
- 결과: 얻는 것은 root·capability 없음. 감수하는 것은 배치가 `/run`·데이터 볼륨을 999 소유로 줘야 한다는 점(선언에 있다)과, 기존 root 소유 데이터 볼륨은 한 번 소유를 999 로 바꿔야 뜬다는 점(운영 데이터 이전은 deploy 몫, 이 영역은 운영 볼륨을 갖고 있지 않다). 0035 의 TLS 감시는 같은 사용자라 `gosu kill` 대신 `kill -HUP` 이다.
- 시험: `tests/test_database_image.py` 는 capability 를 하나도 더하지 않고(cap-drop ALL) `/run` 을 999 소유 tmpfs 로 주어 기동하고, 이미지 USER·실행 중 프로세스·선언의 uid/gid 가 같음을 본다. 변이: Dockerfile 에서 USER 를 지우면 기동 실패. 측정하지 않은 것: 배치 영역(deploy)에서의 실제 기동(E2E 는 이 영역이 돌리지 않는다).
- 되돌리는 조건: 배치 영역이 쓰기 경로를 999 소유로 줄 수 없게 되거나 공식 이미지가 non-root 기동을 막을 때.

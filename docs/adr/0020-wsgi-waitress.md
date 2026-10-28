# 0020. 수집·읽기·검토 면은 WSGI 앱을 waitress로 서비스한다

- 상태: 채택(헤드 결정 D111). [0010](0010-stdlib-http-server.md)을 대체한다(Supersedes 0010).
- 맥락: 0010은 `http.server.ThreadingHTTPServer`에 연결 상한(`BoundedServer`)과 소켓 timeout을 직접 얹었다. Python 문서는 `http.server`를 운영용으로 권하지 않는다고 하고, 연결 상한·느린 클라이언트 방어는 표준 서버가 이미 하는 일이다(표준성 점검 D1·D2).
- 결정: 앱을 WSGI(PEP 3333) 함수(`make_app`)로 바꾸고 waitress 3.0.2로 서비스한다. 직접 만든 `BoundedServer`(연결 상한 스레드 서버)와 `BaseHTTPRequestHandler` 처리를 지웠다. 세 면(수집·읽기·검토)이 한 앱이다.
- 왜: WSGI는 Python 웹 서버와 앱의 표준 접점이고, waitress는 순수 Python이며 단일 프로세스·다중 스레드다. 이 서비스는 DB 연결 하나를 RLock으로 직렬화하는 설계(ADR 0008, 표준성 점검 A7)라 프로세스를 나누는 prefork(gunicorn)는 맞지 않는다. waitress는 요청 읽기를 비동기 루프가 하므로 느린 클라이언트가 일꾼 스레드를 잡지 않는다(slowloris 완화).
- 값과 출처 (모두 측정 안 함):
  - `threads` = `connection_limit` = `OPS_MAX_CONNECTIONS`(기본 64, 0010 시점의 값을 유지, 임의값). 연결마다 일꾼이 있던 이전 동작을 유지해, DB 호출이 막혀도 `/healthz`가 대답한다. waitress 기본 4를 쓰지 않은 이유다.
  - `connection_limit`에 +2: waitress는 내부 map(듣는 소켓 + 깨우기 trigger + 연결) 크기로 상한을 잰다. 상한 3에서 연결 1개만 받는 것을 실측해(이 환경) 상한 그대로 받도록 보정했다.
  - `channel_timeout` = `REQUEST_TIMEOUT` 30초(임의 보수값, ADR 0008), `cleanup_interval` = min(5초, channel_timeout)(기본 30초면 timeout이 최대 두 배 늦다. 시험으로 확인).
  - `max_request_body_size` = 8 MiB(= 기존 `MAX_BODY`). 검토 면의 1 MiB 상한은 앱이 그대로 검사한다.
  - 종료: SIGTERM에 듣는 소켓을 닫고 일꾼이 하던 요청을 끝낼 때까지 최대 8초(docker stop 기본 10초보다 짧게, 제약값), 응답이 나가길 최대 1초(임의) 기다린 뒤 연결을 닫는다. waitress `close()`는 일꾼을 5초만 기다리고 응답 전송을 기다리지 않아 얇은 `Server.shutdown`으로 순서를 잡았다.
- 동작 변화(이유): (1) 연결 상한을 넘으면 이전에는 즉시 503이었고 이제는 waitress가 받지 않고 대기열(backlog)에 둔다(waitress에 즉시 거절 설정이 없다). 상한이 찬 동안 새 연결은 응답 없이 기다리다 자리가 나면 처리된다. (2) 8 MiB를 넘는 수집 본문은 앱의 400 대신 waitress가 413으로 거부한다(HTTP 의미상 더 정확). (3) `Server` 헤더가 `ops-feedback-ingest`(ident)이다. 나머지(401/403, 오류 본문, 본문 상한, `/readyz`)는 기존 시험이 그대로 통과한다.
- 검토한 대안: gunicorn(prefork라 연결 하나 직렬화 설계와 맞지 않음), uvicorn/ASGI(앱을 async로 다시 써야 함), Flask 등 프레임워크(라우트 5개에 과함).
- 결과: 얻는 것은 직접 만든 서버 코드 제거와 표준 서버의 연결 관리. 감수하는 것은 의존 하나(waitress 3.0.2, `requirements.lock`에 고정)와 위 동작 변화.
- 되돌리는 조건: 대기열 동작이 호출자(survey-service의 전송 timeout)에 문제가 되거나 처리량이 측정되어 부족하면 앞단 프록시로 상한을 두거나 서버를 바꾼다.

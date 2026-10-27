# 0008. DB 접속·문장·probe 상한 값

- 상태: 채택(FAULT_POLICY 원칙 4·5, FAULT_MATRIX P0-3). 값은 **측정하지 않은 추정**이다.
- 맥락: DB가 TCP는 받되 응답이 없으면 문장이 무한 대기하고 `/readyz`도 막혔다.
- 결정: 접속 3초(`OPS_DB_CONNECT_TIMEOUT`), 문장 3초(`OPS_DB_STATEMENT_TIMEOUT`), `/readyz` probe 2초(`OPS_DB_PROBE_TIMEOUT`), 스키마 생성 30초(`OPS_DB_DDL_TIMEOUT`). 문장 상한은 서버 쪽 `statement_timeout`과, 서버가 얼면 +1초 뒤 클라이언트가 소켓을 닫는 이중 장치다. `/readyz`는 주 연결·락을 쓰지 않는 새 연결로 확인하고 결과를 1.5초 공유한다.
- 왜: probe 2초는 FAULT_POLICY 원칙 5("의존이 죽으면 `/readyz`는 2초 안에 503")가 정한 값이다. 접속·문장 3초는 survey-service가 같은 문제에 쓴 값(survey-service `pg.py`와 그 boundary.md)과 맞춘 것이고, 이 영역 문장이 인덱스 있는 단건 INSERT/SELECT와 한 배치 조회라는 코드 읽기에서 온 추정이다. **정상 지연은 측정 안 함.** DDL 30초는 다른 인스턴스의 advisory lock 대기를 감안한 값으로 **근거 기록 없음(임의)**. 읽기 timeout이 소켓 단위라 총 시간 상한이 필요해서 롤백 평가 전체 60초(`rollback.deadline_seconds`)를 따로 둔다: 이 60초도 **근거 기록 없음(임의)**, 설정으로 바꾼다. 소켓 요청 timeout 30초와 동시 연결 64개(`OPS_MAX_CONNECTIONS`)도 **측정 안 함, 임의 보수값**이다. 배치 크기 500(`job.batch_size`)도 봉투 최대 8 MiB를 감안한 임의값이다.
- 대안: 상한 없음 -> 장애 때 무한 대기(관찰: 수정 전 `/readyz`가 15초 안에 응답하지 못함). 서버 쪽 `statement_timeout`만 -> 서버가 얼면(`docker pause`) 듣지 못한다.
- 결과: 얻는 것은 장애 때 상한 있는 실패(기본값 `/readyz` 2.0초, 수집 최악 7.0초 실측). 감수하는 것은 느린 디스크에서 정상 문장이 3초를 넘으면 UNAVAILABLE로 보인다는 점(호출자가 재시도).
- 되돌리는 조건: 실제 지연이 측정되면 값을 그에 맞춰 재정한다(U10과 같은 취급).

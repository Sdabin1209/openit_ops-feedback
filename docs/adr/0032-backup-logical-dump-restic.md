# 0032. 백업: psycopg COPY 논리 덤프를 restic 으로 platform 백업 저장소(REST)에 보낸다

- 상태: 채택(코드 단계 C2, 계약 3.4.6 RUNTIME R10.1, CONVENTIONS 13절 "데이터베이스는 영역의 컴포넌트").
- 맥락: 이 영역의 PostgreSQL(원본 로그, 실패 사례)은 이 영역의 컴포넌트다. 백업은 영역이 자기 데이터를 꺼내 자기 영역 게이트웨이 출구(CONNECT 터널, R1.8)로 platform 백업 저장소(HTTP, restic REST)에 보낸다. 주소·자격 이름은 공통 `OPENIT_BACKUP_URL`, `OPENIT_BACKUP_CREDENTIALS_FILE`, `OPENIT_BACKUP_REPO_PASSWORD_FILE`(계약 CommonEnv, 3.4.5)다.
- 결정:
  1. 명령 `python -m ops_feedback.admin backup`(잡 선언 `ops-feedback-backup`, `timeout_seconds` 1800, 멱등: 실행마다 스냅숏이 하나 늘 뿐 상태를 바꾸지 않는다).
  2. 저장소마다 REPEATABLE READ 읽기 전용 트랜잭션 하나에서 표별 `COPY ... TO STDOUT`을 `COPY 표 FROM STDIN;` 텍스트로 이어 쓴 임시 파일(`log.copy`, `failure.copy`)을 만들고 `restic backup --stdin`으로 보낸다. 스키마는 `admin migrate`가 만드므로 데이터만 낸다. 복구 = migrate 뒤 이 파일을 psql 로 되돌림(복구 시험은 하지 않았다, 아래 감수).
  3. restic 은 `HTTPS_PROXY=OPENIT_EGRESS_PROXY`(Go 표준 프록시 변수)로 영역 게이트웨이 CONNECT 터널을 쓴다. 하위 프로세스 환경은 필요한 변수만 새로 만든다(R9.1). 자격은 파일에서 읽어 `RESTIC_REPOSITORY`(rest:https://사용자:암호@호스트/경로)로만 넘긴다: argv·로그·출력에 없다. 저장소가 없으면 `restic init`.
  4. 보존: 백업 직후 `restic forget --keep-within <N>d --prune`(태그 ops-feedback). N = 원본 보존 기간 `OPS_RAW_RETENTION_DAYS`(없으면 ADR 0007의 원본 기본 30일), 소수는 내림, 무기한·0·숫자 아님은 거부한다. 근거: 스냅숏에는 원본 로그가 들어 있으므로 백업이 원본보다 오래 남으면 원본 보존 기간(ADR 0007)이 무의미해진다(데이터 최소화, 개인정보 보호 원칙). 보존 기간은 데이터의 주인(이 영역, DATA_OWNERSHIP)이 정한다. ADR 0007이 적은 deploy 쪽 "14일 일일 백업"(D88/U4)은 확인하지 못했고, 이 규칙은 그 상한이다(더 짧게 두려면 `OPS_RAW_RETENTION_DAYS`를 낮추는 것이 아니라 원본 보존이 정한다). 백업 저장소(rest-server)는 추가 전용(append-only)을 쓰지 않는다는 것이 헤드의 결정이다(platform 에 전달됨). 추가 전용이면 forget이 실패해 결과가 failed(75)가 된다.
  5. 복구: 복구 잡 `python -m ops_feedback.admin restore [--snapshot ID|latest]`(잡 선언 `ops-feedback-restore`, 주기 없이 수동 실행, DATA_OWNERSHIP 백업 행: 복구는 데이터 주인의 복구 잡만). 스냅숏을 꺼내 `OPS_*_DATABASE_URL_FILE`가 가리키는 새 DB(`admin migrate`로 스키마만 만든 빈 DB)에 한 트랜잭션으로 COPY 한다. 대상 표가 비어 있지 않으면 거부한다(FAILED_PRECONDITION, 덮어쓰지 않는다). 결과와 종료 코드는 백업과 같다(BackupJobResult, 0·75·1).
  6. 문장 상한 1800초 = 잡 상한. 측정하지 않은 시작값이다(R9.3 기본 3600초보다 짧게 두어 겹침 방지가 다음 주기를 막지 않게 한다).
- 왜: restic 은 REST 서버 프로토콜(백업 저장소 쪽 표준), 암호화·중복 제거를 갖춘 표준 도구라 직접 만들지 않는다. pg_dump 를 쓰지 않는 이유는 클라이언트 버전이 서버 버전보다 낮으면 거부하기 때문이고(이미지에 서버 버전을 묶어야 함), 이 영역의 표가 넷뿐이라 COPY 로 충분하기 때문이다. 일관성은 PostgreSQL 문서의 REPEATABLE READ 스냅숏이다.
- 검토한 대안: pg_dump -Fc(버전 결합, 이미지에 클라이언트 추가), 파일 시스템 스냅숏(컨테이너 안에서 못 한다), 직접 만든 업로더(암호화·중복 제거를 다시 만든다).
- 결과: 얻는 것은 영역 소유의 백업(다른 영역 계정 없음). 감수하는 것: (1) 원본 로그(개인정보 포함)가 백업에 들어간다 — restic 저장소 암호로 암호화되고 보존은 위 4번이 원본 보존 기간으로 묶는다. 과거 스냅숏에서 세션 삭제(admin delete-session) 요청을 되돌릴 수는 없다: 삭제 요청 뒤 남은 스냅숏은 원본 보존 기간 안에 사라진다. (2) 복구는 실제 restic·rest-server로 시험했다(`tests/test_backup_restic.py`: 백업 → 스냅숏 → restore → 새 DB에 COPY → 행 확인). 복구는 복구 잡이 한다(위 5번, 실제 rest-server 왕복 시험). (3) 이미지에 restic 패키지가 들어간다(Debian 패키지 버전, 고정하지 않음).
- 되돌리는 조건: 표가 커져 덤프가 상한을 넘으면 증분 방식(WAL 보관)으로.

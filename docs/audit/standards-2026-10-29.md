# ops-feedback 표준성·근거 전수 점검 (2026-10-29, D89)

기준서: `head/docs/audit/standards-audit-spec.md`(헤드가 읽기를 허락). 읽기 전용 점검이며 코드·설정·시험은 수정하지 않았다. 비밀 값(환경 변수, 토큰, secrets/, .env)은 읽지 않았다. 시험은 실행하지 않았다(정적 읽기만).

읽은 것: `CLAUDE.md`, `README.md` 6절, `docs/design.md` 2·3·4절 일부, `docs/boundary.md`·`docs/runbook.md` 해당 부분(grep), `docs/adr/0001~0015` 전부, `src/ops_feedback/` 의 `api.py`, `deid.py`(앞 130줄과 300줄 이후), `detect.py`, `job.py`(1~130, 270~348), `config.py`, `pgbase.py`, `storage.py`, `clock.py`, `rollback.py`, `schemas.py`, `review.py`(20~70), `admin.py`(일부), `logstore.py`·`failurestore.py`(DDL·SQL 위주), `Dockerfile`, `scripts/*`, `config/job.example.yaml`, `pyproject.toml`, `requirements.*`. 이웃 읽기: `../boundary-contracts`의 `canonical.py`, `secrets.py`, `HTTP.md` 일부.

표기: "표준 이름"은 내가 확실히 아는 문서만 적었고, 모르면 "확인 못 함"이라 썼다. ADR이 "표준"이라고만 적고 이름을 대지 않은 곳은 그대로 지적했다. 분류 기준은 S / S- / N / N- / O / ? (기준서 5절).

## 결과표

### A. 저장소·데이터 모델

| ID | 요소 | 분류 | 표준 이름 | 근거 위치 | 증거 위치 |
| --- | --- | --- | --- | --- | --- |
| A1 | 운영 저장소 PostgreSQL | S | 서버형 RDBMS 운영 관행(ADR 0001이 "특정 문헌 없음"이라 자인) | ADR 0001 왜, design.md 2절(사용자 결정) | logstore.py:236-243, failurestore.py:242-249, storage.py:20-26 |
| A2 | 개발·시험 저장소 SQLite(표준 라이브러리) | S | sqlite3 표준 라이브러리, 워크스페이스 D22 | ADR 0001 왜 | logstore.py:139-151, failurestore.py:149-161 |
| A3 | 저장소 구현 셋(메모리·SQLite·PostgreSQL)을 인터페이스 뒤에 두고 SQL 방언 두 벌(`?`/`%s`) 유지 | N | 없음(적합성 시험 방식) | ADR 0001 결정·결과(대안 비교 있음) | logstore.py:30-55, tests/test_conformance_*.py |
| A4 | 원본(log)과 사례(failures) 저장소를 둘로 분리(이중 저장) | S | 최소 권한 원칙(NIST SP 800-53 AC-6) | ADR 0002 왜 | storage.py:20-28, deid.py:355-377 |
| A5 | 읽기 면(`/v1/cases` 등)이 DB 수준 읽기 전용 계정 없이 쓰기 가능한 단일 접속으로 동작. 서버 한 프로세스가 원본·사례 두 접속 정보를 모두 가질 수 있음 | N | 최소 권한과 어긋남. ADR이 자인("읽기 제한이 DB 권한이 아니라 코드 관례") | ADR 0002 결과·되돌리는 조건("계정 분리는 deploy 결정") | api.py:336-341(한 URL로 사례 저장소 개방), runbook.md:130(앱 계정에 SELECT/INSERT/UPDATE/DELETE) |
| A6 | JSON을 TEXT 열로 저장하고 필터용 열 몇 개만 분리. 타임스탬프도 TEXT(RFC 3339 문자열 비교) | N- | 표준 이름 확인 못 함(PostgreSQL은 JSONB·timestamptz를 제공) | 주석 한 줄("SQLite와 바이트까지 같다")만 있음. ADR 없음, 대안(JSONB, timestamptz) 비교 없음 | pgbase.py:4, failurestore.py:242-249, logstore.py:147 |
| A7 | 동시성 모델: 저장소마다 연결 1개 + RLock으로 모든 DB 호출 직렬화, 연결 풀 없음. 서버는 연결당 스레드(상한 64)지만 DB 호출은 한 줄로 선다 | N- | 연결 풀(psycopg_pool 등)이 표준 대안. 선택 이유 확인 못 함 | 근거 기록 없음(ADR 없음. ADR 0008은 timeout만 다룸) | pgbase.py:79,113-140, api.py:65-87 |
| A8 | 스키마 관리: 시작 때 `CREATE TABLE IF NOT EXISTS` 멱등 실행 + advisory lock, `admin migrate`는 같은 DDL. 스키마 버전 표·ALTER 경로 없음 | N- | 마이그레이션 도구(Alembic 등)가 표준 대안. O 후보였으나 직접 만든 양이 작아 N-로 분류 | 근거 기록 없음(runbook.md:130에 사용법만) | pgbase.py:84-105, admin.py:80-83 |
| A9 | 스키마 생성에 PostgreSQL advisory lock, 키 7264001 | S- | PostgreSQL 문서화된 advisory lock 기능 | 주석 "이 영역 고정값"만, 숫자 선택 이유 없음(무해) | pgbase.py:15,87-91 |
| A10 | 수집 멱등 키 `UNIQUE(session_id, turn_id)` + `ON CONFLICT DO NOTHING` | S | PostgreSQL `INSERT ... ON CONFLICT`(문서화된 기능) | ADR 0009 | logstore.py:249, api.py:308-311 |
| A11 | 해시·정규화: RFC 8785(JCS) + SHA-256(`sha256_hash`) | S | RFC 8785, FIPS 180-4 | boundary-contracts canonical.py 머리 주석(계약 소유) | job.py:21,121, logstore.py:353 |
| A12 | 파생 ID의 해시 절단 길이: `fc-`/`tr-` 32 hex(128비트), `rb-`/`metric-` 24 hex(96비트) | N- | 표준 이름 없음 | 근거 기록 없음(길이 선택 이유가 어디에도 없음) | job.py:112,147, rollback.py:85,94 |
| A13 | 목록 커서 = 정수 seq(불투명하지 않음) | S- | 표준 이름 확인 못 함(관행: keyset pagination) | 근거 기록 없음 | failurestore.py:391-397 |

### B. 비식별

| ID | 요소 | 분류 | 표준 이름 | 근거 위치 | 증거 위치 |
| --- | --- | --- | --- | --- | --- |
| B1 | 허용 목록(default deny) + 잔존 패턴 검사 실패 시 닫음(fail-closed) | N | ADR은 "개인정보 보호의 표준 설계 원칙(fail-closed)"이라 쓰지만 **이름 있는 문서를 대지 않았다**. 내가 이름을 붙일 수 있는 문서도 확인 못 함 | ADR 0004 왜·대안(차단 목록, 모델 기반 비교) | deid.py:1-11, deid.py:379-395 |
| B2 | 키 이름 목록 기반 필터: `keep_string_keys`(question_id, slot_id, repeat_key, code, path, action, next_item)가 식별자 모양 정규식에 전체 일치할 때만 남김. 그 밖의 문자열은 `[MASKED]` | N | 표준 없음(이 영역의 설계). 한계: 영문 이름 모양 식별자는 구별 못 함(ADR이 자인) | ADR 0004 결정·결과, 실측 근거 = 점검 S1(`next_item="홍길동 환자"` 통과) | deid.py:36-48, deid.py:310-325 |
| B3 | 패턴 길이 한도(64, 160, 키 32 등)와 문자 집합 | N- | 표준 없음 | ADR 0004·0013이 **"근거 기록 없음, 보수적 임의값"**이라 자인 | deid.py:36-48, deid.py:52 |
| B4 | **의사 식별자 구성**: `"p-" + SHA-256(JCS({salt, key, value}))의 앞 32 hex`. 키드 해시를 직접 구성했고 표준 HMAC이 아니다 | O | 키 있는 해시의 표준은 HMAC(RFC 2104, FIPS 198-1). 가명화 지침에서 키드 해시/HMAC을 권하는 것으로 알지만(ENISA 가명처리 기법 보고서 등) 문서 내용을 이 점검에서 대조하지 못했다. 같은 리포 `api.py:21`이 이미 stdlib `hmac`을 import한다 | 근거 기록 없음. ADR 0004는 salt 해시를 **결정 한 줄로만** 쓰고 HMAC과 비교하지 않는다. deid.py:346-349 docstring과 admin.py는 "한 함수로 통일"(OF-03)만 설명 | deid.py:346-349, job.py:304-310 |
| B5 | 의사 식별자를 SHA-256의 128비트로 절단(`[7:39]`) | N- | 표준 이름 없음 | 근거 기록 없음 | deid.py:349 |
| B6 | salt 관리: 전역 salt 하나, 길이·생성 방법·엔트로피 요구 없음(비어 있지만 않으면 통과), 회전 절차 없음(바꾸면 `admin delete-session`이 0건, 기존 사례와 연결 끊김), 키 보관 분리(가명정보의 추가정보 분리) 문서 없음 | N- | 표준 이름 확인 못 함 | 근거 기록 없음. salt를 파일로 받는 것은 D84(비밀 처리)일 뿐 salt 정책이 아니다. admin.py:95 메시지가 회전 영향의 일부만 경고 | job.py:304-310, deid.py:355-359, admin.py:40-45,86-95 |
| B7 | 잔존 패턴 집합(이메일, `phone_kr`, `rrn_kr`). `rrn_kr`는 뒷자리 첫 숫자를 `[1-4]`로 제한 | N- | 표준 없음. 참고(확인 못 함, 사실 확인 필요): 내가 알기로 외국인등록번호 뒷자리는 5~8로 시작하므로 이 패턴이 놓칠 수 있다. 근거 문서는 대조하지 못했다 | 근거 기록 없음. 예시 설정의 값이며 ADR 0004는 "이메일·전화·주민번호"만 열거 | job.example.yaml:52-55, review.py:29-32, deid.py:388-393 |
| B8 | 긴 문자열 4,096자 창 + 겹침 1,024로 잔존 검사 | N | 표준 없음 | ADR 0011(값은 측정 안 함 명시, 대안 비교) | deid.py:21-30,388-393 |
| B9 | 검토자 입력(`expected`, `reviewed_input`)은 비식별기를 거치지 않고 이메일·전화·주민번호 패턴 **차단 목록**으로만 거름. 문자열 상한 20,000 | N | 표준 없음. 차단 목록 방식은 ADR 0004가 비식별에는 "권하지 않는다"고 한 방식이다 | ADR 0004 결과(이름은 사람 책임). 20,000은 "임의 상한, 측정 안 함" 주석 | review.py:20-32 |
| B10 | `extract_protections` 건수 맵: 키가 `[A-Z][A-Z0-9_]{0,31}`이고 값이 0 이상 정수인 항목만 남김 | N | 표준 없음 | ADR 0013(대안 a~d 비교, 한계 자인) | deid.py:52,300-312 |
| B11 | `pause_reason` 열거 3값 정규화, 모르는 값은 OTHER | S | 계약 PUBLIC_API.md H절(boundary-contracts 2.1.0) | ADR 0014 | deid.py:380-386, detect.py:36-48 |
| B12 | 이 비식별 결과가 법적 가명처리·익명처리 요건을 충족하는지, 의사 식별자와 salt 분리 보관 요건이 적용되는지 | ? | 개인정보 보호법의 가명정보 규정이 관련될 것으로 보이나 조문과 요건을 이 점검에서 확인하지 못함 | ADR에 법적 기준 대조 기록 없음 | ADR 0004 전체, design.md 4절 |

### C. 보존

| ID | 요소 | 분류 | 표준 이름 | 근거 위치 | 증거 위치 |
| --- | --- | --- | --- | --- | --- |
| C1 | 보존 기본값 원본 30일, 사례 180일, 지표 365일 | N | 표준 이름 없음. 출처 = D88/U4(사용자 결정). 측정 아님. ADR이 쓴 "개인정보 최소 보존"은 일반 원칙이며 문서 이름은 없음 | ADR 0007 왜("독자적으로 근거를 만든 값이 아니다"). D88 본문은 읽기 허용 밖이라 확인 못 함 | config.py:201-205, job.example.yaml:78-84 |
| C2 | `failed_raw_days` 기본값 없음(적지 않으면 raw_days 따름) | N | 표준 없음 | ADR 0007(이전 90일 임의값 폐기 이력) | config.py:206,227-231 |
| C3 | 처리 표시 없는 원본은 지우지 않고 경보, `raw_after_processing: minimize` 옵션 | N | 표준 없음 | ADR 0007 왜·대안 | job.py:95-108,199-225, logstore.py:19 |
| C4 | 기한 계산 = `received_at` 기준 UTC 문자열 비교, 잡 안에서 삭제 | S- | RFC 3339 UTC | 근거 기록 없음(자명) | job.py:204-225 |
| C5 | 법정 보존·파기 기준(개인정보 파기 의무, 의료 기록 관련 보존기간)과 30/180/365일의 정합, 백업 보존(14일, deploy)과의 정렬 | ? | 확인 못 함 | ADR 0007이 "백업 정렬은 확인하지 못했다"고 자인 | ADR 0007 되돌리는 조건 |

### D. 서버·프로토콜·인증·운영

| ID | 요소 | 분류 | 표준 이름 | 근거 위치 | 증거 위치 |
| --- | --- | --- | --- | --- | --- |
| D1 | 서버 구현 = 표준 라이브러리 `http.server.ThreadingHTTPServer` | N- | 내가 아는 한 Python 문서가 `http.server`를 운영용으로 권하지 않는다고 경고한다(이 점검에서 문서를 다시 열어 확인하지는 못함) | ADR 0010이 **"선택 당시 근거 기록 없음"**을 자인. 현재 판단(의존 최소)은 있으나 처리량 측정 안 함, 대안은 FastAPI 한 줄 | api.py:31,65-87,318, ADR 0010 |
| D2 | 동시 연결 상한·503 응답을 `BoundedServer`로 직접 구현 | O | WSGI/ASGI 서버(gunicorn, uvicorn 등)와 리버스 프록시가 제공하는 기능. 이 점검에서 문서 대조는 못 함 | ADR 0010이 "프레임워크" 대안만 비교하고 운영용 앱 서버·프록시와는 비교하지 않음 | api.py:65-87 |
| D3 | 값: 연결 64, 소켓 30초, readyz 캐시 1.5초, 수집 본문 8 MiB, 검토 본문 1 MiB, 목록 limit 500, 배치 500, DDL 30초, 롤백 평가 60초, SQLite busy_timeout 5000ms | N- | 표준 없음 | ADR 0008·코드 주석이 대부분 **"임의, 측정 안 함"**이라 자인. 8 MiB·limit 500의 출처는 계약일 수 있으나 코드/ADR에 출처 없음 | api.py:47-49,90-91,347, job.py:78,239-241, pgbase.py:23, logstore.py:142 |
| D4 | DB 접속·문장 3초, readyz probe 2초 | N | probe 2초는 FAULT_POLICY 원칙 5(내부 문서, 외부 표준 아님). 3초는 survey-service 값과 정렬 + 코드 읽기 추정 | ADR 0008(정상 지연 측정 안 함 명시, 실측: readyz 2.0초, 수집 최악 7.0초) | pgbase.py:20-23, ADR 0008 |
| D5 | 문장 상한 이중 장치: 서버 `statement_timeout` + 클라이언트 타이머로 소켓 강제 shutdown | N | `statement_timeout`은 PostgreSQL 문서화된 설정(S). 소켓 shutdown 타이머는 직접 구현. libpq의 TCP 수준 설정(keepalive, tcp_user_timeout)과는 비교하지 않았다(내가 아는 범위, 문서 대조 못 함) | ADR 0008 대안(서버 쪽만으로는 `docker pause`를 못 듣는다) | pgbase.py:43,48-57,125-135 |
| D6 | 연결 끊김 때만 재연결 후 같은 호출 1회 재시도 | N | 표준 없음. 재시도 안전성 근거는 docstring(멱등 쓰기) | pgbase.py:113-123 docstring | pgbase.py:126-140 |
| D7 | 인증: `Authorization: Bearer` 정적 토큰, 상수 시간 비교(`hmac.compare_digest`), 수집/읽기/검토 토큰 분리와 서로 달라야 함 | S- | 헤더 형식 RFC 6750(OAuth 2.0 Bearer). 정적 공유 비밀에 이 형식을 쓰는 것은 관행. 상수 시간 비교는 널리 알려진 관행(문서 이름 확인 못 함) | 계약 HTTP.md 11행이 형식을 정함. 선택 이유는 어디에도 없음 | api.py:53-55,114-115,168-172,247,296 |
| D8 | 토큰 수명주기: 발급·만료·폐기·회전 없음. 평문 값을 파일에서 읽어 메모리 맵에 보관. 회전 절차는 사용자 결정 대기 | N- | OAuth 2.0 토큰 발급 서버, mTLS 등이 표준 대안(어느 쪽이 적합한지는 이 점검에서 판단 못 함) | README.md:94 D-A "토큰 회전 포함 사용자 결정"으로 열려 있음. 선택 근거 ADR 없음. 발급 주체가 이 리포인지 deploy인지 확인 못 함 | api.py:326-328,94-102, README.md:94 |
| D9 | 읽기·검토 토큰을 `역할=토큰,역할=토큰` 한 문자열로 받아 `,`와 `=`로 파싱(토큰에 쉼표가 있으면 불가) | N- | 표준 없음 | 계약 HTTP.md 11행이 형식 정의. 선택 이유 없음 | api.py:94-102 |
| D10 | 토큰 없음/불일치를 **403**으로 응답(401·WWW-Authenticate 아님). `/healthz`, `/readyz`는 무인증 | N- | RFC 9110은 유효한 자격이 없을 때 401을 정의하고 403은 자격이 이해되었으나 거부될 때 쓴다(내가 아는 범위) | 계약 HTTP.md 45,119,138행이 403을 정함(소유 = 헤드). 이유 기록 없음 | api.py:45-46,159-166,297 |
| D11 | 전송 암호화(TLS) 없음: 서버는 평문 HTTP로 열림 | ? | TLS 종단을 deploy/프록시가 맡는지 이 리포에서 확인 못 함. 문서에 언급 없음 | 근거 기록 없음, grep 결과 TLS/https 언급 0건 | api.py:105,318, boundary.md·runbook.md 전체 grep |
| D12 | 검토자 신원 = 클라이언트가 보낸 `X-Openit-User` 헤더를 게이트웨이가 인증했다고 신뢰 | N | 표준 없음(신뢰 경계 설계). 게이트웨이가 같은 이름 헤더를 지운다는 전제는 HTTP.md 86행 | D88/U7, HTTP.md 86행 | api.py:273-278 |
| D13 | 로그: 표준 출력 한 줄 JSON, 본문 미기록, 접근 로그는 경로만. `JsonFormatter`는 `exc_info`를 싣지 않아 `log.exception`의 스택이 사라짐 | S- | 12-factor 11장 "로그는 이벤트 스트림" | 주석(요청 경로로 줄 위조 방지)만, ADR 없음 | api.py:58-62,148-149 |
| D14 | SIGTERM에서 `shutdown`, 쓰기는 자동 커밋 단일 문장 | S- | 컨테이너 종료 신호 관행(12-factor 9장 disposability) | 주석 | api.py:364-365 |
| D15 | `/healthz`, `/readyz` 분리, readyz는 새 연결로 확인 | S | 워크스페이스 D22, 쿠버네티스형 probe 관행 | CLAUDE.md(상위)·ADR 0008 | api.py:225-241, pgbase.py:145-169 |

### E. 잡·탐지

| ID | 요소 | 분류 | 표준 이름 | 근거 위치 | 증거 위치 |
| --- | --- | --- | --- | --- | --- |
| E1 | 잡 실행 모델: 한 번 실행하고 끝나는 명령(`run-once`), 스케줄은 deploy. 자체 스케줄러·큐 없음(가져다 쓰는 쪽) | S | 12-factor 12장(관리/일회성 프로세스)로 매핑 가능(이 매핑은 내 제안이며 ADR은 이 이름을 대지 않고 D39와 "큐-워커 분리 원칙"만 적음) | ADR 0003, D39 | job.py:1-5,320-345 |
| E2 | 잡 멱등성: 입력에서 정해지는 case_id·trace_ref·created_at, 이미 있으면 `skipped_diff`로 셈 | S | 멱등 처리 관행(내부 FAULT_POLICY 원칙 2·12 참조, 외부 문서 이름 확인 못 함) | ADR 0003·0006·0015 | job.py:111-112,121,147,277 |
| E3 | 탐지 규칙 집합: 비식별 뒤 구조 필드만 보는 결정적 규칙 8개 | N | 표준 없음 | ADR 0005(모델 기반은 후속으로 대안 비교) | detect.py:149-158 |
| E4 | `REASK_STREAK` 임계값 3, REASK·CONFIRM 합산 연속 | N | 값의 출처 = survey-service의 `SURVEY_MAX_CONSECUTIVE_REASK` 기본 3(이웃 영역 설정과 정렬). 측정 안 함(ADR이 명시). 서비스 값이 바뀌면 여기도 손으로 따라 바꿔야 함 | ADR 0012 결정 1(대안 2, 4 이상, 문항 단위 비교) | detect.py:113-146, job.example.yaml:69-71 |
| E5 | ADR 0012와 예시 설정이 3의 근거로 든 "표준 대화 설계의 '되묻기 2~3회 뒤 사람 연결'" | ? | **이름 있는 표준·문헌이 인용되지 않았고 내가 확인하지 못했다.** 막연한 "표준" 표현의 전형 | ADR 0012, job.example.yaml:70 | 동일 |
| E6 | `EXTRACT_FAILED`: 값이 빈 추출은 REDIRECT만 정상 | N | 표준 없음. 출처 = 계약 스키마(ActionExtraction)와 survey-service 문서 인용. 이 영역은 학습 데이터를 직접 보지 않음(자인) | ADR 0012 결정 2, 시험으로 고정 | detect.py:89-110 |
| E7 | 롤백: 요청만 만들고 `auto_submit` 기본 false, YAML 불리언만 허용, 기준값에 코드 기본값 없음 | N | 표준 없음(사람 승인 기본 = D88/U11) | ADR 0006(대안 비교) | rollback.py:26,104, config.py:248-254 |

### F. 비밀·빌드·배포·그 밖

| ID | 요소 | 분류 | 표준 이름 | 근거 위치 | 증거 위치 |
| --- | --- | --- | --- | --- | --- |
| F1 | 비밀을 `NAME_FILE` 경로로 받고 `read_secret`으로 읽음, DB URL도 같음, 오류·로그에 값 없음 | S | `*_FILE` 관행(Docker secrets), 12-factor 3장은 환경 변수를 권하므로 이 점은 D84가 의도적으로 갈라짐 | D84, runbook.md:63 | storage.py:20-25, api.py:326-328, job.py:304-310, smoke.sh:25-28 |
| F2 | 설정 파일 안 비밀 직접 기입 차단 = 키 이름에 `token/secret/password...`가 든 문자열 값이면 거부(이름 목록 휴리스틱) | N | 표준 없음 | 주석 D84, 한계 설명 없음 | config.py:9-26 |
| F3 | 이미지: 다단계 빌드, 비root(uid 10001), 라벨 6개, HEALTHCHECK, 설정은 환경 변수 | S | Docker 빌드 관행(비root `USER`), 워크스페이스 D22·D50 | CLAUDE.md(상위) | Dockerfile:1-29 |
| F4 | 베이스 이미지가 태그(`python:3.12-slim`)만 있고 digest 고정 없음 | S- | 재현 가능한 빌드 관행 | 근거 기록 없음 | Dockerfile:2,10 |
| F5 | 의존 잠금 `requirements.lock`에 해시 없음, `pip freeze` 후 수작업 갱신 | S- | pip 해시 검증 모드(`--require-hashes`)가 존재함(내가 아는 범위) | 주석(D22) | requirements.lock:1 |
| F6 | 계약 패키지를 이미지에 넣는 방법: 로컬 `../boundary-contracts`에서 `git archive <태그>`로 wheel 생성(워크스페이스 규칙은 `pip install git+ssh@태그`) | N | 상위 CLAUDE.md의 설치 방식에서 벗어남 | 스크립트 주석("docker 안 ssh가 어려워서")만, ADR 없음 | scripts/build_wheels.sh:1-9, Dockerfile:4-8 |
| F7 | 문진 스키마(TurnTrace.json)를 이 리포에 복사해 보관 | N | 상위 CLAUDE.md는 계약 스키마 복사본을 만들지 말라고 함(문진 스키마는 패키지가 아니라 예외로 보임) | 스크립트 주석(컨테이너가 리포 밖을 못 봄, 패키지화는 survey-service 과제) | scripts/sync_vendor.sh:1-11 |
| F8 | 핵심 의존: jsonschema, PyYAML(`safe_load`), psycopg3 바이너리 휠(3.3.6 고정) | S- | 널리 쓰이는 라이브러리, `yaml.safe_load` 관행 | ADR에 선택 비교 없음(psycopg 고정 주석만) | pyproject.toml:9, config.py:211, requirements.lock |
| F9 | 스모크 시험: 동적 포트, 컨테이너·이미지 이름에 PID, 라벨·비root·비밀 비노출 확인 | S | 워크스페이스 D22 | 스크립트 주석 | smoke.sh:12-30 |
| F10 | 관리 CLI(`admin`)의 인증 = 비밀(DB URL, salt) 소유 | N | 표준 없음 | admin.py 머리 docstring("인증 = 비밀 소유") | admin.py:1-14 |
| F11 | 시간: UTC, 초 단위 RFC 3339 `Z`, 주입 가능한 `Clock` | S- | RFC 3339 | 근거 기록 없음(자명) | clock.py:1-18 |

## 분류별 개수 (총 63)

| 분류 | 개수 | ID |
| --- | --- | --- |
| S | 12 | A1, A2, A4, A10, A11, B11, D15, E1, E2, F1, F3, F9 |
| S- | 10 | A9, A13, C4, D7, D13, D14, F4, F5, F8, F11 |
| N | 22 | A3, A5, B1, B2, B8, B9, B10, C1, C2, C3, D4, D5, D6, D12, E3, E4, E6, E7, F2, F6, F7, F10 |
| N- | 13 | A6, A7, A8, A12, B3, B5, B6, B7, D1, D3, D8, D9, D10 |
| O | 2 | B4, D2 |
| ? | 4 | B12, C5, D11, E5 |

## N-, O, ? 목록

N- (표준이 아니고 근거도 없다)
- A6: JSON을 TEXT, 타임스탬프를 TEXT로 저장. JSONB/timestamptz와 비교 없음, 근거는 주석 한 줄. pgbase.py:4
- A7: DB 연결 1개 + 락 직렬화, 풀 없음. 동시성 모델 근거 없음. pgbase.py:79,129
- A8: 스키마 버전 관리 없는 시작 시 `IF NOT EXISTS` DDL. 마이그레이션 도구와 비교 없음(O 후보). pgbase.py:84-105
- A12: 파생 ID 해시 절단 길이(128/96비트) 근거 없음. job.py:112,147, rollback.py:85,94
- B3: 식별자 패턴 길이 한도(64, 160, 32)를 ADR이 임의값이라 자인. deid.py:36-48
- B5: 의사 식별자를 128비트로 절단한 이유 없음. deid.py:349
- B6: salt가 전역 하나, 생성·길이·회전·분리 보관 정책 없음. job.py:304-310, deid.py:355-359
- B7: 잔존 패턴 집합 근거 없음, 주민번호 `[1-4]`가 외국인등록번호를 놓칠 수 있음(내 지식 기준, 확인 필요). job.example.yaml:52-55
- D1: stdlib `http.server`를 운영 서버로 선택. ADR이 선택 당시 근거 없음을 자인. api.py:65
- D3: 연결 64, 소켓 30초, 본문 8 MiB/1 MiB, 배치 500, DDL 30초, 롤백 60초 등 임의값(자인). ADR 0008
- D8: 토큰 발급·만료·폐기·회전 없음, 정적 평문 비밀. 회전은 사용자 결정 대기. api.py:326-328, README.md:94
- D9: `역할=토큰,...` 한 문자열 파싱 형식의 이유 없음(계약 정의). api.py:94-102
- D10: 인증 실패를 403으로 응답(RFC 9110는 401). 계약이 정한 것이라 헤드 소유일 수 있음. api.py:297

O (가져다 써야 할 것을 직접 만들었다)
- B4: 키드 해시를 `SHA-256(JCS({salt,key,value}))`로 직접 구성. HMAC(RFC 2104)과 비교한 기록 없음. 128비트 절단이라 길이 연장 공격은 직접 영향이 작아 보이나, 그것을 따져 본 기록도 없다(내 판단, 미검증). deid.py:346-349
- D2: 연결 상한·503 처리를 직접 구현(`BoundedServer`). 운영용 앱 서버/프록시와 비교한 기록 없음. api.py:65-87

? (표준 여부 확인 못 함)
- B12: 비식별 결과가 법적 가명처리·익명처리 요건(추가정보 분리 보관 포함)을 충족하는지 확인 못 함
- C5: 30/180/365일이 법정 보존·파기 기준 및 백업 보존과 맞는지 확인 못 함
- D11: TLS 종단이 어디서 이뤄지는지 이 리포에서 확인 못 함(평문 HTTP 서버)
- E5: 3의 근거로 인용된 "표준 대화 설계의 되묻기 2~3회" 출처 없음

## 점검하지 못한 것

- 시험을 실행하지 않았다. 코드가 문서대로 동작하는지는 확인하지 않았고 정적으로 읽은 범위만 근거다.
- 읽지 않은 파일: `tests/` 전부, `admin.py`의 purge 구현, `failurestore.py`·`logstore.py`의 메모리/SQLite 세부, `job.py` 130~270(사례 게시·삭제 흐름), `docs/CHANGELOG.md`, `docs/STATUS.md`, `docs/change-requests/*`(CR-005 수집 전송 포함), `docs/design.md`의 대부분. 여기에 숨은 선택(예: 재시도·정렬·락)이 더 있을 수 있다.
- 읽기 허락 밖이라 확인 못 함: `head/DECISIONS.md`의 D39, D84, D88(U4, U7, U11), FAULT_POLICY. ADR이 이들을 근거로 인용한 부분은 인용의 존재만 확인했고 내용은 대조하지 못했다.
- 외부 표준의 정확한 요구는 대조하지 못했다(RFC 2104, 6750, 8785, 9110, 12-factor, ENISA·개인정보 보호법 등은 기억에 의존). 표에서 "내가 아는 범위"라 쓴 곳은 이 점검 중 원문을 다시 열어 확인하지 않았다.
- deploy의 실제 구성(스케줄 주기, DB 계정 권한, 토큰 발급·회전, TLS, 백업)은 이 리포 밖이라 점검하지 않았다.
- 이웃 영역 설정값(survey-service의 `SURVEY_MAX_CONSECUTIVE_REASK` 등)은 ADR 인용만 봤고 직접 확인하지 않았다.
- 데이터 모델의 인덱스 적정성, 쿼리 성능, 처리량은 측정하지 않았다(ADR도 측정 안 함).

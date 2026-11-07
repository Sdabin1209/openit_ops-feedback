# ops-feedback (운영 관측·피드백)

문진 서비스가 운영 중 남긴 기록(trace)을 받아 개인정보를 지우고, 잘못된 턴(실패 사례)을 가려내 다른 영역에 돌려주는 영역이다.

운영 중인 문진 서비스는 매 턴의 입출력 기록을 이 영역의 수집 서비스(ingest-api)로 보낸다. 이 영역은 원본을 `log.db`에 쌓고, 주기 잡이 비식별 처리기로만 원본을 읽어 지운 사본에서 실패 규칙을 찾아 `FailureCase`로 `failures.db`에 게시한다. 이웃 영역(합성 데이터, model-lab, 문항 저작)은 읽기 인터페이스(I7)로 이 사례를 받아 간다. 사람 검토 결과는 새 version으로 게시한다. 실패율이 기준을 넘으면 릴리스 게이트에 롤백을 "요청"한다.

하지 않는 것: 롤백을 직접 수행하지 않는다(활성 포인터는 게이트만 바꾼다). 실패의 원인을 고치지 않는다. 경계 계약과 문진 스키마를 고치지 않는다(필요하면 `docs/change-requests/`). 지표 대시보드, 피드백 배포기, 검토 화면(UI)은 아직 없다(docs/STATUS.md).

## 1. 전체 속의 위치

| 방향 | 이웃 영역 | 인터페이스 | 내용 |
| --- | --- | --- | --- |
| 입력 | survey-service | I6 TraceIngest, `POST /v1/traces` | `TraceEnvelope`(한 턴의 기록). 같은 (session_id, turn_id)는 DUPLICATE |
| 입력 | release-gate | I4 ReleaseReader (읽기) | 활성 포인터, 릴리스 목록. 사례의 `question_set` 채움 |
| 출력 | release-gate | I5 `request_rollback` | `RollbackRequest`(근거 지표 참조 포함) |
| 출력 | synthetic-data, model-lab, 문항 저작 | I7 FailureStore, `GET /v1/cases`, `/v1/cases/{id}`, `/v1/deidentified-traces/{ref}` | `FailureCase`(비식별 뒤), 비식별 trace |
| 입력 | 검토자 | `POST /v1/cases/{id}/review` | 검토 결과(새 version 게시) |

출처: docs/brief.md, docs/boundary.md. I4/I5의 실제 SDK 연결은 release-gate 배포 뒤라 아직 시험 대역만 쓴다(docs/STATUS.md).

## 2. 구조

| 경로 | 역할 |
| --- | --- |
| `schemas/`, `schemas/examples/` | 영역 내부 타입의 JSON Schema와 통과·거부 예시. 스키마 밖 조건 표는 `schemas/README.md` |
| `src/ops_feedback/types/` | `schemas/`에서 생성한 코드 타입(손으로 고치지 않는다) |
| `src/ops_feedback/components/` | 컴포넌트(파일 하나에 업무 하나). 목록과 입출력은 `docs/components.md`. 이웃과의 통신은 `link_*` |
| `src/ops_feedback/wiring/` | 조립: 잡 한 번(`pipeline.py`), 롤백 평가, 검토, 수집 조립, 서버 서비스 면, 저장소 고르기, 스키마 검증 도구 |
| `src/ops_feedback/{api,job,admin}/__main__.py` | 진입점: ingest-api 기동, 주기 잡 1회, 관리 명령 |
| `src/ops_feedback/config.py`, `migrate.py`, `migrations/` | 설정 읽기, DB 마이그레이션 |
| `config/job.example.yaml` | 잡 설정 예시(값은 사용자가 정한 기준이 아님) |
| `tests/` | 수용 시험과 적합성 시험(`tests/fakes.py`는 이웃 대역) |
| `examples/` | 입출력 예시 JSON |
| `scripts/` | `smoke.sh`(이미지 스모크), `build-image.sh`(영역 빌드 진입점), `build_wheels.sh`(폐기 예정), `make_examples.py` |
| `Dockerfile` | ingest-api 이미지 |
| `docs/` | 설계, 경계, 런북, 상태, 변경 이력, 변경 요청 |

```
survey-service --I6--> ingest-api --> log.db (원본)
                                          | 비식별 처리기만 읽음
                                          v
              주기 잡: 비식별 -> 탐지 -> 분류 -> 게시 --> failures.db --I7--> 이웃 영역
                                          |
                          롤백 평가 --I4 읽기 / I5 요청--> release-gate
```

## 3. 실행 방법

전제: Python 3.10 이상(`pyproject.toml`), git. 스모크와 이미지에는 Docker. 이웃 폴더는 필요 없다. 계약 패키지는 `requirements.txt`의 Git 태그로 설치되므로 GitHub(SSH)에 접근할 수 있어야 한다.

```
scripts/setup-env.sh                                            # 설치 (.venv 생성·재사용, 핀대로 맞춤, 여러 번 실행해도 안전)
.venv/bin/python -m pytest -q                                   # 시험 (PostgreSQL 시험은 건너뜀으로 표시된다)
scripts/dev-postgres.sh                                         # 일회용 PostgreSQL 컨테이너 위에서 전체 시험 (make test-pg)
scripts/smoke.sh                                                # 이미지 빌드, 기동, 수집, 인증 확인 (Docker 필요)
```

- ingest-api: `OPS_INGEST_TOKEN_FILE=/run/secrets/ingest OPS_LOG_DB=/data/log.db python -m ops_feedback.api`(`api/__main__.py`) (토큰은 파일에 두고 경로만 준다, D84. `OPS_INGEST_TOKEN=값` 직접 지정은 개발용으로만 동작한다).
- 잡 1회: `OPS_DEID_SALT_FILE=/run/secrets/deid_salt python -m ops_feedback.job --config config/job.yaml` (`config/job.example.yaml`을 복사해 사용. 옵션 `--limit --reprocess --retry-deid-failed --no-rollback`).
- 비밀(D84): 운영에서는 토큰과 salt를 파일에 두고 경로만 `OPS_INGEST_TOKEN_FILE`, `OPS_READ_TOKENS_FILE`, `OPS_REVIEW_TOKENS_FILE`, `OPS_DEID_SALT_FILE`로 준다. 잡 설정 파일에 비밀 값을 적으면 잡이 시작을 거부한다. 상세는 docs/runbook.md.

이 문서를 쓰는 세션에서 실제로 돌린 것: `.venv/bin/python -m pytest --collect-only -q`(195건 수집, 이후 시험이 늘었다), `.venv/bin/python -m ops_feedback.job --help`(옵션 목록 출력). 돌리지 않은 것: pytest 실행 전체, `scripts/smoke.sh`, 이미지 빌드, ingest-api 기동, 잡 실행, `pip install`.

## 4. 이 영역의 열린 항목

출처: `head/docs/OPEN_GAPS.md`(읽기 전용으로 읽음). 이 영역 이름이 직접 적힌 것만 골랐다.

| ID | 요약 | 막고 있는 것 |
| --- | --- | --- |
| G14 | 헤드 결정을 반영함(ADR 0012, 0013, 0019): REASK_STREAK은 서비스의 pause_reason을 읽음(threshold 3은 폐기), 빈 값 HOLD·SKIP은 실패, extract_protections 건수 보존. 값이 실데이터로 맞는지는 측정 전 | 운영 데이터 측정 |
| G11 | 문진 스키마 정수 버전 1 안의 비호환 변경. survey-service와 ops-feedback은 같은 잠금으로 함께 배포해야 한다(D79) | 다른 영역 의존(survey-service), 운영 데이터 전에 TurnTrace 버전 필드 검토 |
| D-A | 원본 보관 기간 등(운영 도메인, 토큰 회전 포함) | 사용자 결정 |
| D-E | 회귀 평가가 읽는 v2 TurnTrace 입력 키 이름 확정 | 다른 영역 의존(survey-service 확정 후) |

시험 건수는 문서에 적지 않고 실행 명령으로 센다(`.venv/bin/python -m pytest -q`, PostgreSQL 포함은 `make test-pg`). 두 번째 머신에서의 실행은 아직 하지 않았다.

GPU가 막는 항목은 없다. 이 영역의 문서(docs/STATUS.md, docs/runbook.md)에는 위 외에 비식별 기준, 분류체계 O11, 허용 중단 시간 O16, 롤백 기준 수치가 사용자 결정 대기로 적혀 있으나, OPEN_GAPS에는 이 ID로 올라 있지 않다(미확인). 영역 문서에서 비식별 기준은 이미 "O 번호 없음"으로 적혀 있고, O11·O16도 대장 번호 없음이다(O11·O16이라는 이름의 출처는 확인하지 못했다).

## 5. 시험 상태 (코드 단계 C2, 2026-11-04)

이 절이 이 영역 시험 상태의 정본이다(건수는 실행 명령으로 센다, 다른 문서에 복사하지 않는다). 고정 버전은 `requirements.txt`가 정본이다(여기에 복사하지 않는다). 로컬 태그는 `GIT_CONFIG_GLOBAL=head/shared/gitconfig-local-mirrors`로 받았다.

실제로 돌린 것(이 환경 한 곳, WSL2):
- `scripts/dev-postgres.sh`(PostgreSQL 16 컨테이너 위 전체): rc=0, 건너뜀 0(통과 수는 실행 시점 값이다, 건수는 실행 명령으로 센다).
- `scripts/smoke.sh`(이미지 빌드, `/healthz`, 수집, 잡 1회, restic 포함 이미지): rc=0 `SMOKE OK`.
- 실제 restic과 표준 restic rest-server 컨테이너로 백업 -> 스냅숏 -> 복구 잡(`admin restore`) -> 복구한 DB의 행 확인: `tests/test_backup_restic.py` 통과(ADR 0032).
- `tests/test_database_image.py`(ops-feedback-db 이미지를 실제로 띄워 선언 계정 verify-full 접속, dml 계정 DDL 거부, 접속 규칙·권한, 인증서 갱신 재읽기)와 `tests/test_build_image.py`의 db 변형: 변이 16건(접속 규칙 trust, PUBLIC CONNECT, 슈퍼유저, 시퀀스 권한, CREATE 권한, ssl off, 접속 수, 인증서 검사, 빈 암호, 데이터 폴더 hba, 라벨, 감시 끔/HUP 없음, build-image 라벨·고정 버전) 모두 시험이 실패함을 확인했다(처음 생존 2건은 시험을 고쳐 재확인). 결과는 build/logs/(커밋하지 않는다).
- 이번에 넣거나 바꾼 시험은 시험마다 변이 확인을 했다(일부러 어기면 실패하는지): 변이 41건(시험당 1~2개) 가운데 처음 생존한 5건(읽기 전용 DB, forget 상한, 보존 기본값, 두 저장소 덤프, 앱 계정 verify)은 시험을 고쳐 모두 실패함을 확인했다. 변이 도구는 리포 밖에서 돌렸고 결과 표는 커밋 메시지에 있다.

돌리지 않은 것·알려진 한계:
- 두 번째 깨끗한 환경에서의 시험·스모크.
- 컨테이너 조합(deploy)에서의 재측정과 platform 백업 저장소(실제 platform 구성)와의 연결: 헤드·deploy 몫.
- 타입 생성(손으로 고치지 않는다): `.venv/bin/pip install -r requirements-dev.lock && .venv/bin/python scripts/gen_types.py`. 구조 검사: `tests/test_structure.py`, 헤드 도구 `head/scripts/structure-check.py ops-feedback`.
- 이웃 장애·중복·중단 칸의 현재 동작: docs/boundary.md "장애 칸별 현황"(실제 HTTP 가짜 상류로 시험, 실제 이웃 서비스와는 연결하지 않음). v2 TurnTrace 전체 키에 대한 규칙 동작은 이 세션에서 다시 확인하지 않았다.

## 6. 설계 결정 기록 (ADR, D89)

구조·기본값·한도를 고른 이유는 `docs/adr/`에 있다. 측정하지 않은 값과 근거를 찾지 못한 값은 각 ADR에 그렇게 적었다.

| ADR | 결정 |
| --- | --- |
| [0001](docs/adr/0001-sqlite-dev-postgres-prod.md) | 개발·시험은 SQLite, 운영은 PostgreSQL |
| [0002](docs/adr/0002-separate-raw-and-failure-stores.md) | 원본 저장소와 실패 사례 저장소 분리 |
| [0003](docs/adr/0003-ingest-and-job-split.md) | 수집 서버와 주기 잡 분리 |
| [0004](docs/adr/0004-deid-allowlist.md) | 비식별 허용 목록 방식, 필드별 처리, 값 검증 |
| [0005](docs/adr/0005-detection-rules.md) | 실패 탐지 규칙과 임계값(자리표시자 표시) |
| [0006](docs/adr/0006-rollback-request-auto-submit.md) | 롤백은 요청만, auto_submit 기본 false |
| [0007](docs/adr/0007-retention-defaults.md) | 보존 기본값(D88/U4) |
| [0008](docs/adr/0008-db-timeouts.md) | DB 접속·문장·probe 상한과 그 밖의 한도 |
| [0009](docs/adr/0009-idempotency-key.md) | 수집 멱등 키 (session_id, turn_id) |
| [0010](docs/adr/0010-stdlib-http-server.md) | 표준 라이브러리 HTTP 서버(0020이 대체) |
| [0011](docs/adr/0011-deid-long-string-windows.md) | 비식별 결과의 긴 문자열: 거부 대신 창 검사 |
| [0012](docs/adr/0012-reask-streak-and-empty-values.md) | REASK_STREAK 임계값 3(측정 안 함), 빈 값 HOLD·SKIP은 실패 |
| [0013](docs/adr/0013-extract-protections-counts.md) | extract_protections 건수는 식별자 모양 키+정수만 남김 |
| [0014](docs/adr/0014-pause-reason.md) | 정지 사유 pause_reason 보존, 지표 분리, 서비스가 이미 호출한 사건의 중복 방지 |
| [0015](docs/adr/0015-response-fallback-rule-id.md) | 규칙 ID GENERATOR_FALLBACK을 RESPONSE_FALLBACK으로 개명, 기존 사례는 skipped_diff |
| [0016](docs/adr/0016-pseudonym-hmac-sha256.md) | 의사 식별자는 HMAC-SHA256(키=salt) |
| [0017](docs/adr/0017-salt-policy.md) | salt 32바이트 이상, 기동 때 길이 검사, 회전은 재가명화 절차 |
| [0018](docs/adr/0018-numeric-values-provenance.md) | 숫자 값의 출처 분류(측정·표준·제약·임의), 값은 바꾸지 않음 |
| [0023](docs/adr/0023-remove-scrub-drop-options.md) | 비식별 설정의 scrub·drop·count_map_key_pattern 제거(계약 3.0.0) |
| [0022](docs/adr/0022-build-entrypoint.md) | 영역 빌드 진입점 scripts/build-image.sh(계약 2.5.0 U42) |
| [0021](docs/adr/0021-yoyo-migrations.md) | 스키마는 yoyo-migrations(0001의 시작 DDL 대체) |
| [0020](docs/adr/0020-wsgi-waitress.md) | WSGI 앱 + waitress(0010 대체) |
| [0019](docs/adr/0019-reask-limit-from-trace.md) | 되묻기 한도 정지는 trace의 pause_reason을 읽는다(0012 결정 1 대체) |
| [0024](docs/adr/0024-schemas-at-repo-root-installed-as-data.md) | 영역 스키마는 리포 루트 `schemas/`, 설치본에는 data-files |
| [0025](docs/adr/0025-component-decomposition.md) | 입력 스키마 하나 -> 출력 스키마 하나인 컴포넌트로 나눈다(U400) |
| [0026](docs/adr/0026-openit-link-for-link-components.md) | 받는 쪽 통신 컴포넌트는 openit_link 위에 쓴다 |
| [0027](docs/adr/0027-inject-clock-and-wait.md) | 시계·타이머·대기를 인자로 주입한다(시험에서 실제 시간 0) |
| [0028](docs/adr/0028-request-deadline.md) | 사람용 요청에 서버 쪽 요청 전체 마감 55.0초(D120, D125 개정) |
| [0029](docs/adr/0029-rollback-evidence-confirmed-only.md) | 롤백 요청의 근거는 사람이 CONFIRMED로 검토한 사례만 센다(L07) |
| [0030](docs/adr/0030-review-utterance-copy.md) | 검토 사본: 환자 발화 두 칸만 원문, reviewer 전용 경로, 읽기마다 접근 기록(L12) |
| [0031](docs/adr/0031-db-owner-and-app-roles.md) | DB 계정 분리: 소유자(DDL, migrate만)와 앱(DML), 서버·잡은 verify만(L08) |
| [0032](docs/adr/0032-backup-logical-dump-restic.md) | 백업: COPY 논리 덤프를 restic 으로 platform 백업 저장소에(R10.1) |
| [0033](docs/adr/0033-gateway-token-identity-header.md) | 사람 신원 헤더는 게이트웨이 토큰에서만 쓴다(CONVENTIONS 6절) |
| [0035](docs/adr/0035-area-database-image.md) | 영역 DB 이미지 ops-feedback-db: 공식 postgres 위에 계정·권한·접속 규칙·TLS, 인증서 갱신 재읽기(D126) |
| [0034](docs/adr/0034-request-body-limits.md) | 경로별 요청 본문 상한을 노출 선언에 적고 서버 상한과 같게 둔다(3.4.7) |

## 7. 더 자세한 문서

| 문서 | 내용 |
| --- | --- |
| `docs/brief.md` | 영역의 목적, 소유·제공·소비, 수용 기준 |
| `docs/components.md` | 컴포넌트 표: 파일, 입력·출력 타입, 하는 일 |
| `docs/design.md` | 내부 설계: 저장소, 수집, 비식별, 탐지, 분류, 롤백 요청, 잡 |
| `docs/adr/` | 설계 결정 기록 |
| `docs/boundary.md` | 소유 저장소, 제공 인터페이스, 지원 버전, 장애 영향, 비밀 전달 |
| `docs/runbook.md` | 설치·실행, 환경 변수, 잡 설정 키, 비밀 NAME_FILE, 장애 대응 |
| `docs/STATUS.md` | 한 일, 시험 결과, 남은 일 |
| `docs/CHANGELOG.md` | 변경 이력 |
| `docs/change-requests/` | 헤드에게 낸 변경 요청(파일 목록이 정본) |
| `CLAUDE.md` | 이 영역 에이전트용 지침 |

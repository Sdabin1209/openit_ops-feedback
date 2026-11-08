# 런북

## 준비

```
scripts/setup-env.sh   # .venv 생성·재사용, requirements.lock·requirements.txt(계약 태그)대로 설치. 멱등
.venv/bin/python -m pytest -q          # 수용 시험
```

문진 스키마(TurnTrace)는 사본이 아니라 패키지 `openit-survey-schema`(requirements.txt의 태그 핀)로 설치한다. 태그를 올리면 `requirements.txt`의 핀 한 줄과 `scripts/setup-env.sh` 재실행으로 끝난다.

## 한눈에: 서비스와 잡이 쓰는 것

| 구분 | ingest-api (`python -m ops_feedback.api`) | 잡 (`python -m ops_feedback.job --config <파일>`) |
| --- | --- | --- |
| 쓰는 경로 | `log.db`(원본, `OPS_LOG_DB`) | `log.db`(처리 표시, 보관 기간 지난 원본 삭제), `failures.db`(사례, 비식별 trace, 지표, 롤백 요청) |
| 읽는 경로 | `failures.db`(읽기 면, `OPS_FAILURE_DB`) | `log.db`(비식별 처리기만), `failures.db` |
| 환경 변수(비밀은 `_FILE`, 아래 절) | `OPS_INGEST_TOKEN`(필수, 수집 쓰기), `OPS_LOG_DB`, `OPS_PORT`, 읽기 면: `OPS_FAILURE_DB`+`OPS_READ_TOKENS=역할=토큰,...` | `OPS_DEID_SALT`(필수, 이름은 `deid.salt_env`), 설정의 `{env: 이름}`이 가리키는 토큰들 |
| 저장소 선택 | `OPS_LOG_DATABASE_URL`(비밀, `_FILE` 우선)이 있으면 PostgreSQL, 없으면 `OPS_LOG_DB`. 읽기 면은 `OPS_FAILURE_DATABASE_URL` 또는 `OPS_FAILURE_DB`. `/readyz`에는 저장소 종류(`postgresql`\|`sqlite`)만 나오고 접속 문자열은 어디에도 나오지 않는다 | 같은 환경 변수 두 개가 설정의 `storage.*_db`(SQLite 경로)보다 우선한다. 서버와 같은 데이터베이스를 가리키면 볼륨 공유가 필요 없다 |
| 볼륨(SQLite일 때만) | `/data`에 `log.db`와 `failures.db`를 둔다(이미지 기본 `OPS_LOG_DB=/data/log.db`, `OPS_FAILURE_DB=/data/failures.db`) | 설정 `storage.log_db`, `storage.failure_db`. ingest-api와 **같은 볼륨**을 가리켜야 한다(SQLite WAL, 두 프로세스가 같은 호스트에서 공유) |
| 네트워크 | 수집 `POST /v1/traces`(수집 토큰), 읽기 `GET /v1/cases`, `/v1/cases/{id}`, `/v1/deidentified-traces/{ref}`(읽기 토큰, 역할별) | I4 읽기와 I5 롤백 호출을 쓰면 release-gate 주소와 토큰 |

읽기 토큰으로 `/v1/traces`를, 수집 토큰으로 읽기 경로를 호출하면 403이다(맞는 토큰이지만 권한 없음). 토큰이 없거나 틀리면 401과 `WWW-Authenticate: Bearer`이고, 검토 게시는 `X-Openit-User`가 없어도 401이다(계약 CONVENTIONS.md 6절, D106). `publish_case`는 HTTP로 열려 있지 않다(잡만 쓴다).

## 잡 설정 파일 키 (YAML, `config/job.example.yaml` 참고)

| 키 | 필수 | 뜻 |
| --- | --- | --- |
| `storage.log_db`, `storage.failure_db` | 예 | SQLite 파일 경로(PostgreSQL URL 환경 변수가 있으면 무시) |
| `deid.policy_id`, `deid.keep_string_keys`, `deid.unlisted_string_action`(mask만) | 예 | 비식별 정책의 필수 부분. 기본값 없음 |
| `deid.salt_env` | 아니오(기본 `OPS_DEID_SALT`) | salt를 담은 환경 변수 이름 |
| `deid.pseudonymize_keys`, `mask_defs`, `drop_defs`, `mask_number_keys`, `mask_value_keys`, `residual_patterns` | 아니오 | 4절 처리 방식의 정책 값 |
| `detection.detectors[]` | 예 | `{type: rule, rules: [{id, enabled, params}]}` (4.0.0 D135: 설정에 factory 탐지기는 없다) |
| `classification.default_failure_type` | 예 | 나머지(`rule_to_type`, `priority`)는 선택 |
| `retention.raw_days` 등 | 아니오(생략하면 기본 30/180/365일, ADR 0007) | 일 수(0 초과) 또는 `"forever"`(무기한, 경고). `null`은 거부 |
| `rollback.*` | 선택(있으면 모두 필수) | `window_hours, min_turns, max_failure_rate, counted_review_statuses, requested_by_role, auto_submit`. `counted_review_statuses`는 `[CONFIRMED]`만 허용한다: 롤백 요청의 근거는 사람이 확정한 사례뿐이다(ADR 0029) |
| `release_reader`, `gate_service` | 선택 | `{kind: http, base_url, token_secret, timeout_seconds?}`(계약 HttpClientSettings, 4.0.0 D135: 구현 모듈 경로는 없다). 토큰은 `token_secret` 이름의 `이름_FILE`로 읽는다. `release_reader`가 있으면 사례에 `question_set`도 채운다 |

## 설정 (사용자 결정 사항 포함)

`config/job.example.yaml`을 복사해 쓴다. 예시의 값은 사용자가 정한 기준이 아니다. 다음은 사용자가 정해야 한다.

- `deid.*`: 남길 키, 정의별 처리, 잔존 패턴. 정하기 전에는 예시(자유 문자열 전부 마스킹)를 쓴다.
- `retention.*`: 일 수면 그보다 오래된 것을 잡이 삭제한다(원본은 처리가 끝난 것만). 무기한은 `"forever"`이고 `null`은 거부한다(계약 `OpsFeedbackJobConfig`). 생략하면 기본값을 쓴다(ADR 0007).
- `classification`: 분류체계(O11, 대장 번호 없음).
- `rollback.*`: 기준 수치.

환경 변수(값은 이미지·리포지토리에 넣지 않는다):

| 변수 | 사용처 | 비고 |
| --- | --- | --- |
| `OPS_INGEST_TOKEN` | ingest-api | 필수. 서비스 토큰 |
| `OPS_LOG_DB` | ingest-api | 기본 `/data/log.db` |
| `OPS_PORT` | ingest-api | 기본 8080 |
| `OPS_HOST` | ingest-api | 바인드 주소(선택, 기본 0.0.0.0, 계약 3.4.2). 127.0.0.1이면 그 주소에만 붙는다 |
| `OPS_FAILURE_DB`, `OPS_READ_TOKENS` | ingest-api | 둘 다 있을 때만 I7 읽기 면을 켠다. 토큰은 `역할=토큰,역할=토큰` |
| `OPS_REVIEW_TOKENS` | ingest-api | 검토 게시(`POST /v1/cases/{id}/review`)용 쓰기 토큰, 역할은 보통 `reviewer`. 읽기 면과 같이 켜진다. 수집·읽기·검토 토큰은 서로 달라야 하고 서로의 경로는 403 |
| `OPS_DB_CONNECT_TIMEOUT`, `OPS_DB_STATEMENT_TIMEOUT`, `OPS_DB_PROBE_TIMEOUT`, `OPS_DB_DDL_TIMEOUT` | ingest-api, 잡 | PostgreSQL일 때만. 초 단위, 기본 3/3/2/30. 의미와 근거는 docs/boundary.md "DB 대기 상한". 잡은 DB 무응답이면 종료 코드 2 |
| `OPS_MAX_CONNECTIONS`, `OPS_READYZ_CACHE_SECONDS`, `OPS_RAW_RETENTION_DAYS`, `OPS_REVIEW_REQUIRE_USER`, `OPS_DB_SCHEMA` | ingest-api | 동시 연결 상한(64), `/readyz` 결과 공유 초(1.5), 원본 보관 기간 선언(잡 `retention.raw_days`와 같은 값, 없으면 시작 로그와 `/readyz`에 경고), 검토 게시에 `X-Openit-User` 필수(기본 1), 스키마 `apply`(기본)\|`verify`(DDL 안 함). `OPS_DB_SCHEMA`는 잡도 같다 |
| `OPS_DEID_SALT` | 잡 | 필수. 의사 식별자 HMAC 키(ADR 0016). **32바이트 이상**이어야 기동한다(ADR 0017). `head -c 32 /dev/urandom | base64 > salt`로 만들어 파일로 준다. 바꾸면 case_id와 trace ref가 모두 바뀌므로 운영 중 바꾸지 않는다. 회전 절차는 ADR 0017 |

## 비밀은 NAME_FILE로 (D84)

토큰과 salt를 환경 변수 값으로 주면 `docker exec env`, `docker inspect`로 평문이 보인다. 운영에서는 값을 파일에 두고 그 **경로**만 `NAME_FILE` 환경 변수로 준다(`boundary_contracts.read_secret`, 파일이 우선이고 없을 때만 `NAME`을 읽는다. `NAME` 직접 지정은 개발용). 서버(ingest-api)와 잡(run-once) 둘 다 같다.

| 비밀 | 파일 경로 변수 |
| --- | --- |
| 수집 토큰 | `OPS_INGEST_TOKEN_FILE` |
| 읽기 토큰(`역할=토큰,...`) | `OPS_READ_TOKENS_FILE` |
| 검토 토큰(`역할=토큰,...`) | `OPS_REVIEW_TOKENS_FILE` |
| 의사 식별자 salt | `OPS_DEID_SALT_FILE` (이름은 `deid.salt_env` + `_FILE`) |
| 설정 `token_secret`이 가리키는 토큰(예: `OPS_GATE_READ_TOKEN`, `OPS_GATE_WRITE_TOKEN`) | `이름_FILE` |

```
docker run ... -v /srv/secrets/ops:/run/secrets:ro -e OPS_INGEST_TOKEN_FILE=/run/secrets/ingest -e OPS_READ_TOKENS_FILE=/run/secrets/read ...
```

파일은 컨테이너 사용자(root 아님)가 읽을 수 있어야 하고, 내용 앞뒤 공백·개행은 잘린다. 파일이 없거나 비어 있으면 값 없이 파일 경로만 담은 오류로 멈춘다(서버는 종료 코드 2). 값은 로그, `/readyz`, 오류 본문, 비식별 산출물에 나오지 않으며 `tests/test_secrets.py`와 `scripts/smoke.sh`(inspect·exec env 검사)가 확인한다.

### 설정 파일에 비밀을 적지 않는다

잡 설정(`config/job.*.yaml`)에서 키 이름에 `token`, `secret`, `password`, `credential`, `api_key`, `authorization`, `private_key`가 들어간 곳에 문자열 값을 직접 적으면 잡이 시작을 거부한다(중첩 dict·list 포함). 오류에는 키 경로(예: `release_reader.token`)와 "비밀은 NAME_FILE로" 안내만 나오고 값은 나오지 않는다. 비밀의 이름만 받는 `*_secret`(예: `token_secret`)·`*_env` 키(`deid.salt_env` 등)는 값이 아니라 이름이라 그대로 쓰고 `read_secret(NAME)`이 읽는다(`NAME_FILE` 우선). 이름만 받는 `*_env` 키(`deid.salt_env` 등)는 그대로 쓴다. ingest-api는 설정 파일 없이 환경 변수만 받으므로 위의 `NAME_FILE` 규칙이 전부다.

## ingest-api 실행

```
OPS_INGEST_TOKEN_FILE=/run/secrets/ingest OPS_LOG_DB=/data/log.db python -m ops_feedback.api   # 값 직접 지정(OPS_INGEST_TOKEN=...)은 개발용
# 컨테이너
bash scripts/build-image.sh <BuildImageRequest JSON>   # 영역 빌드 진입점(ADR 0022): wheel을 만들고 이미지를 빌드해 BuildImageResult 한 줄을 낸다
# 로컬 스모크는 scripts/smoke.sh가 같은 진입점으로 빌드한다. scripts/build_wheels.sh는 폐기 예정이라 쓰지 않는다
docker run -p 8080:8080 -v ops-log:/data -v /srv/secrets/ops:/run/secrets:ro -e OPS_INGEST_TOKEN_FILE=/run/secrets/ingest ops-feedback
```

확인: `curl localhost:8080/healthz`, `/readyz`. 스모크 시험(호스트 포트는 Docker가 배정, 이미지 빌드, 기동, 수집, 중복, 인증): `scripts/smoke.sh` (표준 `docker`만 쓴다. 실행 파일 이름은 `DOCKER` 환경 변수로만 바꾼다. 데몬에 닿지 않으면 오류로 중단한다).

## 잡 실행 (주기는 cron 등 외부에서)

```
OPS_DEID_SALT=... python -m ops_feedback.job --config config/job.yaml
```

출력은 JSON 한 줄. 종료 코드 1이면 `errors`를 본다.

| 증상 | 확인 | 조치 |
| --- | --- | --- |
| `deid_failed` > 0 | 잔존 패턴에 걸림(`errors`에 패턴 이름만 나온다) | 정책 보정 후 `--retry-deid-failed` |
| `skipped_diff` > 0 | 설정이 바뀌어 기존 PENDING 사례와 결과가 다름 | 의도한 변경이면 새 정책 적용 절차를 헤드와 논의(기존 레코드는 불변) |
| `rollback.submit_error` | 게이트 장애 | 다음 실행이 같은 request_id로 재전송 |
| 같은 결과 확인 | `--reprocess`로 다시 돌려 `cases_published: 0` | |

## 검토

HTTP: `POST /v1/cases/{case_id}/review` 본문 `{review_status: CONFIRMED|DISMISSED|PENDING, expected?, expected_kind?, reviewed_input?}`(`reviewed_input`은 개인정보를 뺀 문장으로 검토자가 직접 쓴 입력이며 expected_kind에 맞는 ActionExtractionInput(LORA_A)|ResponseRequest(LORA_B)), `Authorization: Bearer <검토 토큰>`. 201이면 새 version, 같은 검토 재전송은 200과 같은 결과, 없는 사례는 404. `expected`는 CONFIRMED에만 붙일 수 있고 `expected_kind`(LORA_A|LORA_B)가 필수이며, LORA_A면 `ActionExtraction`(객체), LORA_B면 `ResponseText`(문자열)를 지켜야 한다(아니면 400과 사유). 2.0.0부터 비식별 trace에 입력 필드가 있는지는 보지 않는다. `X-Openit-User: <Id 형식>` 헤더가 있으면 그 값이, 없으면 토큰 역할 이름이 `ext["ops-feedback"].reviewed_by`에 새 version마다 기록된다. 같은 검토(review_status·expected·expected_kind·reviewed_input이 최신 version과 같음)의 재전송은 200과 기존 레코드(reviewed_by 불변), 하나라도 다르면 201 새 version이다. 검토자는 읽기 면으로 비식별 trace를 보고 쓴다. 내부 호출은 `ReviewQueue(store).pending()`으로 목록, `review(case_id, "CONFIRMED"|"DISMISSED", role, expected=..., base_version=...)`로 결과 게시(새 version). 검토 화면(UI)은 아직 없다.

## 검토 사본: 환자 발화 원문 (ADR 0030)

- 설정: 잡 설정 `deid.review_utterance: {keep: true, max_chars: 1000}`(예시는 `config/job.example.yaml`). `keep: false`나 생략이면 이전과 같다.
- 읽기: `GET /v1/review-traces/{ref}`는 검토 토큰(reviewer)만, `X-Openit-User` 필수(`OPS_REVIEW_REQUIRE_USER` 기본). 읽기 토큰으로는 403이다. 기존 `GET /v1/deidentified-traces/{ref}`는 발화가 마스킹된 채 그대로다. 게이트웨이 노출은 `deploy-exposure.json`에 선언했다.
- **보존**: 원문 발화는 `failures.db`(aux kind `review_trace`)에 사례와 같은 수명으로 남는다. 가명 정보가 아니라 원문이므로 확정 사례는 `cases_days`(기본 180일) 동안 남고, DB 백업에도 담긴다(백업 보존은 deploy). 세션 삭제(`admin delete-session`)와 보존 정리는 사본도 함께 지운다.
- **접근 기록**: 읽을 때마다 `aux` kind `trace_access`에 누가·역할·언제·trace ref·원문 제공 여부를 남기고, 남기지 못하면 읽기를 거부한다. 보존은 `metrics_days`(기본 365일). 원문 발화는 기록에 없다.
- 정책 지문 영향: `keep: true`는 정책 지문을 바꾼다. 이미 게시된 사례는 내용이 같아 `skipped_diff`가 되지 않고 그대로 두며, 그 사례의 사본은 없다(검토 경로는 마스킹본을 낸다). 사본을 채우려면 원본 `log.db`가 남아 있는 범위에서 `--reprocess`로 잡을 다시 돌린다(사례는 바뀌지 않고 사본만 생긴다).
- 잔존 패턴(전화·주민번호·이메일)이 원문 발화에 걸리면 그 턴은 사례가 되지 않고 `DEID_FAILED`로 남는다. 이름·주소는 패턴으로 알 수 없어 검토자와 접근 통제가 책임이다.
- `reviewed_input`에 보존한 발화 원문을 그대로 붙여 넣으면 400으로 거부된다(읽기 역할 전체가 `FailureCase`로 읽기 때문).

## 롤백 요청

`rollback` 설정과 `release_reader` 팩토리가 있으면 잡이 평가한다. `auto_submit: false`면 요청을 `aux`(kind `rollback_request`)에만 기록한다. 게이트가 실제로 적용했는지는 영수증(kind `rollback_receipt`)을 본다. 수동 개입은 게이트 쪽에서 한다(이 영역은 포인터를 바꾸지 않는다).

## 장애 대응

- ingest-api가 죽었을 때: 재시작. 서비스가 버퍼한 것을 재전송하고 중복은 DUPLICATE로 흡수된다.
- DB 파일 손상: `data/` 볼륨 백업이 정책 밖이다(O16 미정, 대장 번호 없음). 원본(`log.db`) 손실은 복구할 수 없다.
- 원본 접근: 비식별 처리기 외에는 `log.db`를 열지 않는다. 운영 사고로 직접 열어야 하면 개인정보 접근으로 기록한다.

## 계약 4.x 데이터가 남은 저장소 (계약 5.0.0)

계약 5.0.0은 새 환경을 전제하고 4.x 데이터를 이전하지 않는다. 저장소에 `pipeline_schema_version` 4.x 레코드가 남아 있으면 서버(`ops_feedback.api`)와 잡은 `UNSUPPORTED_VERSION` 으로 시작을 거부하고 종료 코드 2 로 끝난다. 새 저장소를 쓰거나 `python -m ops_feedback.admin purge`(admin 은 이 검사를 하지 않는다)로 지운 뒤 다시 띄운다.

## 보존·삭제·스키마 (0.12.0)

- 기본 보존: 원본 30일(처리 끝난 것만), 사례 180일, 지표·롤백 기록 365일(`retention.*`, docs/adr/0007). `null`은 거부, 무기한은 `"forever"`(경고). 보고서 `warnings`에 `RETENTION_FOREVER`, `UNPROCESSED_RAW_PAST_RETENTION`이 나오면 확인한다.
- 기본값 차이: 잡은 확정 사례도 `cases_days` 뒤 지운다(`cases_purge_confirmed: true`), `admin purge`는 `--include-confirmed` 없이는 보존한다. 재시도 대상 원본은 `failed_raw_days`를 적었을 때만 그 기한에, 적지 않으면 `raw_days`에 지운다(기본값 없음). `delete-session`이 종료 코드 3이면 지울 것을 찾지 못한 것이다(salt·정책 확인).
- 요청 시 삭제: `OPS_DEID_SALT_FILE=... python -m ops_feedback.admin delete-session --session-id <ID> [--dry-run]`(저장소는 서버와 같은 `OPS_*_DATABASE_URL[_FILE]`). 출력은 건수만. 지표·롤백 기록(aux)은 세션 정보가 없어 지우지 않는다.
- 수동 정리: `admin purge --raw-days N --cases-days M --metrics-days K [--include-confirmed] [--include-unprocessed]`, `admin minimize-raw`.
- DB 계정 분리(L08, docs/adr/0031): PostgreSQL 계정은 둘이다. 소유자는 DDL만(`admin migrate` 잡 전용), 앱은 스키마 USAGE, 표 SELECT/INSERT/UPDATE/DELETE, 시퀀스 USAGE/SELECT만 가진다. 서버·잡은 `OPS_DB_SCHEMA=verify`만 허용한다(값이 없으면 verify, `apply`는 시작 거부). 스키마가 최신이 아니면 기동을 거부한다. 배포 순서: 소유자 접속 문자열 파일(`OPS_LOG_DATABASE_URL_FILE`, `OPS_FAILURE_DATABASE_URL_FILE`)로 `admin migrate`를 먼저 돌리고, 같은 이름에 앱 접속 문자열 파일을 준 서버·잡을 띄운다. 새 표가 생기면 앱 계정에 권한을 다시 준다(소유자에 `ALTER DEFAULT PRIVILEGES`를 걸어 두면 자동). 시험: `tests/test_db_roles.py`(앱 계정의 CREATE·ALTER·DROP이 InsufficientPrivilege).
- 시험: `scripts/dev-postgres.sh`(또는 `make test-pg`)가 PostgreSQL 컨테이너를 띄워 전체 시험을 돌리고 내린다.

## 잡 선언 라벨 (io.openit.jobs)

이미지 라벨 `io.openit.jobs`는 리포의 `deploy-jobs.json`이 정본이다. `scripts/build-image.sh`가 계약 `JobsDeclaration`으로 검증한 뒤(R7, 어기면 이미지를 만들지 않는다) 한 줄 JSON으로 단다. 선언한 명령·설정 경로·비밀 파일 이름이 코드와 맞는지는 `tests/test_jobs_declaration.py`가 본다. 잡 설정 파일 경로(`/etc/ops-feedback/job.yaml`)와 `needs`(`postgres`)는 이 영역의 제안 값이며 deploy가 아직 이 라벨을 읽지 않는다(deploy가 compose에 직접 적는다).

## TLS와 바깥 호출 (U99)

- 이 서비스는 TLS 서버가 아니다(deploy의 사이드카가 HTTPS를 끝낸다). 바깥 호출(release-gate I4/I5)의 주소는 잡 설정의 `base_url`이며 코드에 고정된 주소는 없다(`tests/test_tls_outbound.py`). https 주소를 쓴다.
- 사설 루트는 표준 환경 변수 `SSL_CERT_FILE`로 믿는다(클라이언트는 `ssl` 기본 검증을 쓰고 검증을 끄는 코드가 없다). 임시 루트와 서버 인증서로 시험한다.
- DB는 접속 문자열(비밀 파일)의 `sslmode`, `sslrootcert`를 그대로 libpq에 넘긴다(코드가 덮어쓰지 않는다). 예: `postgresql://user:pw@host/db?sslmode=verify-full&sslrootcert=/run/secrets/root.pem`.

## 백업과 영역 게이트웨이 출구 (코드 단계 C2, 계약 3.4.6)

- 백업: `python -m ops_feedback.admin backup`(잡 `ops-feedback-backup`, 상한 1800초). 설정은 공통 이름 `OPENIT_BACKUP_URL`(https, restic REST), `OPENIT_BACKUP_CREDENTIALS_FILE`(`<사용자>:<암호>` 한 줄), `OPENIT_BACKUP_REPO_PASSWORD_FILE`. 영역 밖 길은 `OPENIT_EGRESS_PROXY`뿐이다. 근거·감수: docs/adr/0032. 백업은 스냅숏 하나(`ops-feedback.copy`, 파일 안에 `-- store: log`, `-- store: failure` 구획)이고 직후 `restic forget --keep-within <원본 보존 일 수>d --prune`으로 오래된 스냅숏을 지운다. 복구: 복구 잡 `python -m ops_feedback.admin restore [--snapshot ID|latest]`(잡 `ops-feedback-restore`, 수동 실행). 순서: 새 PostgreSQL 에 `admin migrate`(소유자 계정) -> `OPS_LOG_DATABASE_URL_FILE`, `OPS_FAILURE_DATABASE_URL_FILE`를 그 새 DB 로 두고 restore. 대상이 비어 있지 않으면 거부한다. 종료 코드는 백업과 같다(0 ok, 75 재시도 가능, 1 그 밖의 실패). `tests/test_backup_restic.py`가 실제 restic·rest-server 로 백업 -> 복구 잡 -> 행 확인을 시험한다.
- 이웃 호출(release-gate I4·I5): 잡 설정의 클라이언트 생성에 `egress_proxy_from(environ)`을 넘긴다(`wiring/build.py`). `deploy-exposure.json`의 `egress`는 `release-gate`, `external:backup`이다.
- 지표: `GET /metrics`(인증 없음, 저장소가 멈춰도 200)는 카운터 넷(`ops_feedback_{auth_rejected,ingest_accepted,ingest_duplicate,unavailable}_total`)을 낸다.
- 사람 신원 헤더는 역할 `reviewer`의 읽기·검토 토큰(게이트웨이 토큰)에서만 쓴다(docs/adr/0033).

## 영역 DB 이미지 ops-feedback-db (D126, ADR 0035, 0036)

- 실행 사용자: root 가 아닌 postgres(999:999), capability 없음(ADR 0036). 배치는 `/run`·`/tmp`·데이터 폴더를 999 소유로 주고 비밀·인증서는 보조 그룹으로 읽게 한다(`db/inputs.json`).
- 빌드: `bash scripts/build-image.sh <요청 JSON>`에 `"variant": "db"`(소스 `db/`). 라벨 `io.openit.database`가 선언(`db/database.json`)이다.
- 입력(계약 AreaDatabaseEnv): `POSTGRES_PASSWORD_FILE`(슈퍼유저), `DB_ACCOUNT_SECRETS_DIR`(기본 /run/secrets/db, 계정마다 파일 하나: `ops_log_owner`, `ops_log_app`, `ops_failure_owner`, `ops_failure_app`), `DB_TLS_DIR`(기본 /certs, `tls.crt`·`tls.key`). 서버·잡 컨테이너는 `OPS_LOG_DATABASE_URL_FILE`, `OPS_FAILURE_DATABASE_URL_FILE`에 앱 계정 접속 URI(`sslmode=verify-full`)를, `admin migrate` 컨테이너는 같은 이름에 소유자 계정 URI 를 받는다.
- 인증서를 갱신하면 `DB_TLS_DIR` 파일을 원자적으로 바꾼다. 컨테이너는 5초 안팎(두 번 연속 확인)에 새 인증서를 읽는다. 재시작이 필요 없다.
- 건강 확인: 이미지의 HEALTHCHECK 가 선언 계정의 TLS 접속과 서버 인증서 파일을 본다(`docker inspect` 의 State.Health). deploy 는 이것을 쓰고 pg_isready 로 대신 확인하지 않는다.
- 한계: 계정 암호는 처음 초기화할 때만 적용된다(회전 없음). 시험: `tests/test_database_image.py`(이미지를 실제로 띄워 선언 계정으로 verify-full 접속, dml 계정 DDL 거부, 접속 규칙, 인증서 재읽기).

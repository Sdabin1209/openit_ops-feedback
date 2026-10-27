# 런북

## 준비

```
python3 -m venv .venv
.venv/bin/pip install -e ../boundary-contracts -e '.[dev]'
.venv/bin/python -m pytest -q          # 수용 시험
```

이웃 스키마 사본 갱신: `scripts/sync_vendor.sh` (그 뒤 pytest의 `test_schema_sync`가 통과해야 한다).

## 한눈에: 서비스와 잡이 쓰는 것

| 구분 | ingest-api (`python -m ops_feedback.api`) | 잡 (`python -m ops_feedback.job --config <파일>`) |
| --- | --- | --- |
| 쓰는 경로 | `log.db`(원본, `OPS_LOG_DB`) | `log.db`(처리 표시, 보관 기간 지난 원본 삭제), `failures.db`(사례, 비식별 trace, 지표, 롤백 요청) |
| 읽는 경로 | `failures.db`(읽기 면, `OPS_FAILURE_DB`) | `log.db`(비식별 처리기만), `failures.db` |
| 환경 변수(비밀은 `_FILE`, 아래 절) | `OPS_INGEST_TOKEN`(필수, 수집 쓰기), `OPS_LOG_DB`, `OPS_PORT`, 읽기 면: `OPS_FAILURE_DB`+`OPS_READ_TOKENS=역할=토큰,...` | `OPS_DEID_SALT`(필수, 이름은 `deid.salt_env`), 설정의 `{env: 이름}`이 가리키는 토큰들 |
| 볼륨 | `/data`에 `log.db`와 `failures.db`를 둔다(이미지 기본 `OPS_LOG_DB=/data/log.db`, `OPS_FAILURE_DB=/data/failures.db`) | 설정 `storage.log_db`, `storage.failure_db`. ingest-api와 **같은 볼륨**을 가리켜야 한다(SQLite WAL, 두 프로세스가 같은 호스트에서 공유) |
| 네트워크 | 수집 `POST /v1/traces`(수집 토큰), 읽기 `GET /v1/cases`, `/v1/cases/{id}`, `/v1/deidentified-traces/{ref}`(읽기 토큰, 역할별) | I4 읽기와 I5 롤백 호출을 쓰면 release-gate 주소와 토큰 |

읽기 토큰으로 `/v1/traces`를, 수집 토큰으로 읽기 경로를 호출하면 403이다. `publish_case`는 HTTP로 열려 있지 않다(잡만 쓴다).

## 잡 설정 파일 키 (YAML, `config/job.example.yaml` 참고)

| 키 | 필수 | 뜻 |
| --- | --- | --- |
| `storage.log_db`, `storage.failure_db` | 예 | SQLite 파일 경로 |
| `deid.policy_id`, `deid.keep_string_keys`, `deid.unlisted_string_action`(mask\|drop) | 예 | 비식별 정책의 필수 부분. 기본값 없음 |
| `deid.salt_env` | 아니오(기본 `OPS_DEID_SALT`) | salt를 담은 환경 변수 이름 |
| `deid.pseudonymize_keys`, `scrub_string_keys`, `scrub_patterns`, `mask_defs`, `drop_defs`, `mask_number_keys`, `mask_value_keys`, `residual_patterns` | 아니오 | 4절 처리 방식의 정책 값 |
| `detection.detectors[]` | 예 | `{type: rule, rules: [{id, enabled, params}]}` 또는 `{factory: "모듈:함수", args}` |
| `classification.default_failure_type` | 예 | 나머지(`rule_to_type`, `priority`)는 선택 |
| `retention.raw_days` | 예 | 숫자 또는 null(삭제 안 함) |
| `rollback.*` | 선택(있으면 모두 필수) | `window_hours, min_turns, max_failure_rate, counted_review_statuses, requested_by_role, auto_submit` |
| `release_reader`, `gate_service` | 선택 | `{factory: "모듈:함수", args: {...}}`. args 값 `{env: 이름}`은 환경 변수 값으로 바뀐다. `release_reader`가 있으면 사례에 `question_set`도 채운다 |

## 설정 (사용자 결정 사항 포함)

`config/job.example.yaml`을 복사해 쓴다. 예시의 값은 사용자가 정한 기준이 아니다. 다음은 사용자가 정해야 한다.

- `deid.*`: 남길 키, 정의별 처리, 잔존 패턴. 정하기 전에는 예시(자유 문자열 전부 마스킹)를 쓴다.
- `retention.raw_days`: 숫자면 그보다 오래된 원본을 잡이 삭제한다. `null`이면 삭제하지 않는다.
- `classification`: 분류체계(O11).
- `rollback.*`: 기준 수치.

환경 변수(값은 이미지·리포지토리에 넣지 않는다):

| 변수 | 사용처 | 비고 |
| --- | --- | --- |
| `OPS_INGEST_TOKEN` | ingest-api | 필수. 서비스 토큰 |
| `OPS_LOG_DB` | ingest-api | 기본 `/data/log.db` |
| `OPS_PORT` | ingest-api | 기본 8080 |
| `OPS_FAILURE_DB`, `OPS_READ_TOKENS` | ingest-api | 둘 다 있을 때만 I7 읽기 면을 켠다. 토큰은 `역할=토큰,역할=토큰` |
| `OPS_REVIEW_TOKENS` | ingest-api | 검토 게시(`POST /v1/cases/{id}/review`)용 쓰기 토큰, 역할은 보통 `reviewer`. 읽기 면과 같이 켜진다. 수집·읽기·검토 토큰은 서로 달라야 하고 서로의 경로는 403 |
| `OPS_DEID_SALT` | 잡 | 필수. 의사 식별자 salt. 바꾸면 case_id와 trace ref가 모두 바뀌므로 운영 중 바꾸지 않는다 |

## 비밀은 NAME_FILE로 (D84)

토큰과 salt를 환경 변수 값으로 주면 `docker exec env`, `docker inspect`로 평문이 보인다. 운영에서는 값을 파일에 두고 그 **경로**만 `NAME_FILE` 환경 변수로 준다(`boundary_contracts.read_secret`, 파일이 우선이고 없을 때만 `NAME`을 읽는다. `NAME` 직접 지정은 개발용). 서버(ingest-api)와 잡(run-once) 둘 다 같다.

| 비밀 | 파일 경로 변수 |
| --- | --- |
| 수집 토큰 | `OPS_INGEST_TOKEN_FILE` |
| 읽기 토큰(`역할=토큰,...`) | `OPS_READ_TOKENS_FILE` |
| 검토 토큰(`역할=토큰,...`) | `OPS_REVIEW_TOKENS_FILE` |
| 의사 식별자 salt | `OPS_DEID_SALT_FILE` (이름은 `deid.salt_env` + `_FILE`) |
| 설정 `{env: 이름}`이 가리키는 토큰(예: `OPS_GATE_READ_TOKEN`, `OPS_GATE_WRITE_TOKEN`) | `이름_FILE` |

```
docker run ... -v /srv/secrets/ops:/run/secrets:ro -e OPS_INGEST_TOKEN_FILE=/run/secrets/ingest -e OPS_READ_TOKENS_FILE=/run/secrets/read ...
```

파일은 컨테이너 사용자(root 아님)가 읽을 수 있어야 하고, 내용 앞뒤 공백·개행은 잘린다. 파일이 없거나 비어 있으면 값 없이 파일 경로만 담은 오류로 멈춘다(서버는 종료 코드 2). 값은 로그, `/readyz`, 오류 본문, 비식별 산출물에 나오지 않으며 `tests/test_secrets.py`와 `scripts/smoke.sh`(inspect·exec env 검사)가 확인한다.

### 설정 파일에 비밀을 적지 않는다

잡 설정(`config/job.*.yaml`)에서 키 이름에 `token`, `secret`, `password`, `credential`, `api_key`, `authorization`, `private_key`가 들어간 곳에 문자열 값을 직접 적으면 잡이 시작을 거부한다(중첩 dict·list 포함). 오류에는 키 경로(예: `release_reader.args.token`)와 "비밀은 NAME_FILE로" 안내만 나오고 값은 나오지 않는다. 비밀은 `{secret: NAME}` 또는 `{env: NAME}`으로 적으면 `read_secret(NAME)`이 푼다(`NAME_FILE` 우선). 이름만 받는 `*_env` 키(`deid.salt_env` 등)는 그대로 쓴다. ingest-api는 설정 파일 없이 환경 변수만 받으므로 위의 `NAME_FILE` 규칙이 전부다.

## ingest-api 실행

```
OPS_INGEST_TOKEN=... OPS_LOG_DB=/data/log.db python -m ops_feedback.api
# 컨테이너
scripts/build_wheels.sh   # git archive v2.0.2 -> wheel (build/wheels)
docker build -t ops-feedback-ingest --build-arg COMMIT=$(git rev-parse --short HEAD) .
docker run -p 8080:8080 -v ops-log:/data -e OPS_INGEST_TOKEN=... ops-feedback-ingest
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

## 롤백 요청

`rollback` 설정과 `release_reader` 팩토리가 있으면 잡이 평가한다. `auto_submit: false`면 요청을 `aux`(kind `rollback_request`)에만 기록한다. 게이트가 실제로 적용했는지는 영수증(kind `rollback_receipt`)을 본다. 수동 개입은 게이트 쪽에서 한다(이 영역은 포인터를 바꾸지 않는다).

## 장애 대응

- ingest-api가 죽었을 때: 재시작. 서비스가 버퍼한 것을 재전송하고 중복은 DUPLICATE로 흡수된다.
- DB 파일 손상: `data/` 볼륨 백업이 정책 밖이다(O16 미정). 원본(`log.db`) 손실은 복구할 수 없다.
- 원본 접근: 비식별 처리기 외에는 `log.db`를 열지 않는다. 운영 사고로 직접 열어야 하면 개인정보 접근으로 기록한다.

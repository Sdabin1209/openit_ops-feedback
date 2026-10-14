# 런북

## 준비

```
python3 -m venv .venv
.venv/bin/pip install -e ../boundary-contracts -e '.[dev]'
.venv/bin/python -m pytest -q          # 수용 시험
```

이웃 스키마 사본 갱신: `scripts/sync_vendor.sh` (그 뒤 pytest의 `test_schema_sync`가 통과해야 한다).

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
| `OPS_DEID_SALT` | 잡 | 필수. 의사 식별자 salt. 바꾸면 case_id와 trace ref가 모두 바뀌므로 운영 중 바꾸지 않는다 |

## ingest-api 실행

```
OPS_INGEST_TOKEN=... OPS_LOG_DB=/data/log.db python -m ops_feedback.api
# 컨테이너
scripts/build_wheels.sh   # git archive v1.0.0 -> wheel (build/wheels)
docker build -t ops-feedback-ingest --build-arg COMMIT=$(git rev-parse --short HEAD) .
docker run -p 8080:8080 -v ops-log:/data -e OPS_INGEST_TOKEN=... ops-feedback-ingest
```

확인: `curl localhost:8080/healthz`, `/readyz`. 스모크 시험(호스트 포트는 Docker가 배정, 이미지 빌드, 기동, 수집, 중복, 인증): `scripts/smoke.sh` (WSL에서 docker가 없으면 `docker.exe`를 쓴다).

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

`ReviewQueue(store).pending()`으로 목록, `review(case_id, "CONFIRMED"|"DISMISSED", role, expected=..., base_version=...)`로 결과 게시(새 version). 검토 화면(UI)은 아직 없다.

## 롤백 요청

`rollback` 설정과 `release_reader` 팩토리가 있으면 잡이 평가한다. `auto_submit: false`면 요청을 `aux`(kind `rollback_request`)에만 기록한다. 게이트가 실제로 적용했는지는 영수증(kind `rollback_receipt`)을 본다. 수동 개입은 게이트 쪽에서 한다(이 영역은 포인터를 바꾸지 않는다).

## 장애 대응

- ingest-api가 죽었을 때: 재시작. 서비스가 버퍼한 것을 재전송하고 중복은 DUPLICATE로 흡수된다.
- DB 파일 손상: `data/` 볼륨 백업이 정책 밖이다(O16 미정). 원본(`log.db`) 손실은 복구할 수 없다.
- 원본 접근: 비식별 처리기 외에는 `log.db`를 열지 않는다. 운영 사고로 직접 열어야 하면 개인정보 접근으로 기록한다.

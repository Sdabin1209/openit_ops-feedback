# 영역 내부 스키마

타입 하나에 파일 하나(`<타입>.schema.json`, JSON Schema draft 2020-12), 통과·거부 예시는 `examples/<타입>.valid*.json`, `<타입>.invalid*.json`. 컴포넌트와 타입의 대응은 [docs/components.md](../docs/components.md).
계약 타입은 복사하지 않고 `../boundary/<이름>.schema.json`으로 가리킨다(검증기가 계약 스키마를 `file:///boundary/`에 올린다, `wiring/validate.py`). 받아들이는 조건(필수, 형식, 열거, 범위, 조건부 필수)은 모두 여기에 있고, 컴포넌트는 입력을 이 스키마로 검증하는 것 하나만 한다.
설정 파일의 키·형식은 계약 `OpsFeedbackJobConfig`가 정한다. 그 계약이 정하지 못한 두 조건(비밀 직접 기입 금지, REASK_STREAK과 keep_pause_reason의 조합)은 `JobConfigChecks`에 있다.

## 스키마 밖 조건

스키마(JSON Schema 2020-12)로 표현하지 못해 코드에 남은 거부 조건이다. 목표는 0이며, 아래는 각각 이유가 있다.

| # | 어디 | 조건 | 왜 스키마로 못 쓰나 |
|---|---|---|---|
| 1 | `trace_intake` | `envelope.trace`가 `runtime_contract_version`에 해당하는 문진 스키마(TurnTrace)를 지킨다 | 문진 스키마는 외부 패키지(openit-survey-schema)이고 어느 버전을 쓸지는 데이터가 정한다. 버전별 `$ref`를 영역 스키마에 박으면 문진 스키마가 바뀔 때 어긋난다(사본 금지, D110) |
| 2 | `trace_intake` | `envelope.runtime_contract_version`이 `supported_versions`에 든다(UNSUPPORTED_VERSION, 422) | 같은 문서 안의 두 값을 비교하는 교차 필드 조건(2020-12에는 값 참조가 없다). 오류 코드가 INVALID_RECORD와 달라야 해서 따로 둔다 |
| 3 | `trace_intake` | `trace.session_id`·`trace.turn_id`가 `envelope`의 것과 같다 | 교차 필드 동일성 |
| 4 | `trace_intake` | `trace.contract_version`이 `envelope.runtime_contract_version`과 같다 | 교차 필드 동일성 |
| 5 | `pseudonymizer` | salt가 UTF-8로 32바이트 이상이다(`check_salt`, ADR 0017) | `minLength`는 문자 수라 바이트 수를 못 센다(한글은 3바이트). 기동 때 한 번 검사한다 |
| 6 | `deidentifier` | 정책이 지목한 `mask_defs`·`drop_defs`가 해당 버전의 문진 스키마에 있다(없으면 DEID_FAILED로 닫는다) | 외부 스키마 안의 이름과의 대조(#1과 같은 이유) |
| 7 | `deidentifier` | 결과에 잔존 패턴(설정의 `residual_patterns`)이 없다. 긴 문자열은 창으로 나눠 본다(ADR 0011) | 입력 조건이 아니라 출력에 대한 검사이고, 패턴이 설정에서 오며 선형 시간 보장을 위해 창으로 나눠 실행해야 한다 |
| 8 | `failure_detector` | `factory`가 돌려준 객체가 `Detector` 인터페이스(`name`, `detect`)를 지킨다 | 임의 Python 객체의 모양은 JSON이 아니다 |
| 9 | `review_decider` | `base_version`이 최신 version과 같다(CONFLICT, 409) | 입력(결정)과 다른 입력(현재 사례)의 교차 비교. 오류 코드가 달라야 한다 |
| 10 | `review_decider` | 검토자가 쓴 값에 이메일·전화·주민번호 패턴이 없다(설정 가능한 패턴, 이메일은 선형 시간 탐지) | 설정으로 바뀌는 패턴이고, 이메일은 정규식이 `@` 없는 긴 문자열에서 제곱 시간이 되어(점검 round2 M2) 직접 선형으로 찾는다 |
| 11 | `review_decider` | 검토 값의 중첩 깊이 64 이하, 객체 키도 패턴 검사 대상이다 | 재귀 깊이 한도와 키 문자열 검사는 JSON Schema로 쓸 수 없다. 문자열 길이(20000자)는 `ReviewDecision.Bounded`에 있다 |
| 12 | `link_ingest_api` | 본문 크기 상한(수집 8 MiB, 검토 1 MiB) | 전송 계층의 한도이고 `openit_link` Route의 `max_body`가 413으로 낸다. 값은 ADR 0008 |
| 13 | `link_gate_send` | 영수증의 `request_id`가 보낸 요청의 것이다 | 교차 필드 동일성(응답 대 요청) |
| 14 | `wiring/build.py` | 설정의 `factory` `모듈:함수`가 실제로 불러와진다 | 모듈 존재 여부는 실행 환경에 달렸다 |
| 15 | `failure_case_store` | version은 최신+1이어야 하고(FAILED_PRECONDITION), 같은 (case_id, version)에 다른 내용을 쓸 수 없으며(CONFLICT), 참조한 비식별 trace가 먼저 저장돼 있어야 한다 | 저장소에 이미 있는 상태에 따른 조건이다(입력 문서만으로 판정할 수 없다). 불변 레코드 원칙(D25) |
| 16 | `failure_case_store` | 목록 `limit`은 1 이상이고 `cursor`는 이전 호출이 낸 불투명 문자열이다 | 문서가 아니라 함수 인자(스키마 대상이 아니다). HTTP 쪽 limit 범위(1~500)는 `ListCasesQuery`에 있다 |
| 17 | `wiring/pgbase.py` | `OPS_DB_*_TIMEOUT`은 0 초과 3600 이하(초), `OPS_DB_SCHEMA`는 apply\|verify | 계약 `OpsFeedbackServerEnv`는 모양(문자열 숫자)만 정하고 범위·값은 읽는 곳이 검사한다(계약 설명). 계약이 범위를 정하면 옮긴다 |
| 18 | `migrate.py` | SQLite 메모리 DB에는 마이그레이션을 적용하지 않는다 | 저장소 경로 문자열의 특수값 규칙(메모리 구현을 쓴다) |
| 19 | `config.py` | `OPS_READ_TOKENS`·`OPS_REVIEW_TOKENS` 형식 `역할=토큰,역할=토큰` | 계약 `OpsFeedbackServerEnv.RoleTokens` 모양 검사 뒤의 분해 규칙이다(문자열을 표로 푸는 구문 해석) |
| 20 | `wiring/build.py` | 설정의 `{secret: NAME}`이 가리키는 비밀이 존재하고 비어 있지 않다 | 비밀 값의 존재는 실행 환경(NAME_FILE)에 달렸다 |
| 21 | `wiring/check_settings.py` | 환경 변수 `OPS_PORT`·`OPS_MAX_CONNECTIONS`·`OPS_READYZ_CACHE_SECONDS`가 숫자로 읽힌다 | 문자열 -> 숫자 변환은 검증 전 단계다(변환 뒤 값은 `ServerSettings`가 검사) |

## 계약이 정한 것을 영역이 같은 모양으로 쓰는 곳

- `REASK_STREAK` 규칙의 `params.threshold`는 계약 `OpsFeedbackJobConfig`가 필수로 요구한다. 이 영역은 값을 쓰지 않는다(ADR 0019). [CR-009](../docs/change-requests/CR-009-reask-params-required.md).

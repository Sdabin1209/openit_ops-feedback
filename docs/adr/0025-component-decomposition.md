# 0025. 영역을 입력 스키마 하나 -> 출력 스키마 하나인 컴포넌트로 나눈다

- 상태: 채택(U400, 헤드 DESIGN.md/D117, 구조 규칙 6개 D104). 이전에는 기능별 모듈(`logstore.py`, `deid.py`, `job.py`, `api.py` 등)이 서로 import했다.
- 맥락: 모듈이 서로의 구조(예: `job.py`가 `deid`, `detect`, `logstore`, `rollback`을 직접 부르고 `review.py`가 `failurestore`를 import)를 알아 한 곳을 바꾸면 이웃 모듈이 영향을 받았다. 입력 검사는 코드 안 `if`·`raise`로 흩어져 있었다.
- 결정: 업무를 단계로 나눠 컴포넌트를 정했다([docs/components.md](../components.md)). 컴포넌트는 `components/`에 파일 하나씩이고, 자기 입력을 `schemas/`의 스키마 하나로 검증하며, 다른 컴포넌트를 import하지 않는다. 연결(어느 출력을 어느 입력에 넘길지, 저장소·salt·클라이언트 주입)은 `wiring/`에 둔다. 상태가 있는 것(로그 저장소, 실패 사례 저장소)과 통신(`link_*`)도 컴포넌트다. 거부 조건은 스키마로 옮기고 옮기지 못한 것은 [schemas/README.md](../../schemas/README.md) 표에 이유와 함께 적는다.
- 왜: 구조 규칙 3·4·5(컴포넌트는 자기 입출력과 업무밖에 모르고, 스키마는 영역이 책임진다). 기능적 핵심/명령형 껍질(functional core, imperative shell, Gary Bernhardt)과 포트·어댑터(Alistair Cockburn)로 같은 입력이면 같은 출력을 내는 업무와 바깥 자원(DB, 네트워크)을 갈랐다.
- 검토한 대안: 기존 모듈 이름을 그대로 컴포넌트로 부르기(서로 import하는 구조가 남는다), 컴포넌트를 더 잘게(예: 탐지 규칙마다 파일, 규칙은 같은 입력·출력 스키마를 공유하는 한 업무의 내부라서 나누지 않았다).
- 결과: 얻는 것은 컴포넌트별 독립 시험(스키마 예시), 거부 조건의 한곳 집중, `tests/test_structure.py`·`head/scripts/structure-check.py`로 구조 위반을 기계가 잡는 것. 감수하는 것은 호출마다 입력을 스키마로 검증하는 비용(측정하지 않았다. 잡이 한 턴당 컴포넌트 7~8번 호출한다)과 시험이 `wiring`을 거치는 보조(`tests/tdeid.py`)를 갖게 된 것. 컴포넌트가 도구 `wiring/validate.py`·`clock.py`·`pgbase.py`를 import하는 것은 컴포넌트끼리의 import가 아니라 도구 사용이라서 허용했다(업무 규칙 없음).
- 되돌리는 조건: 검증 비용이 잡 시간의 주된 부분임이 측정되면 컴포넌트 안쪽 호출의 재검증을 줄이는 방법을 새 ADR로 정한다(내는 쪽 검증, 받는 쪽은 다시 검증하지 않는다 R7).

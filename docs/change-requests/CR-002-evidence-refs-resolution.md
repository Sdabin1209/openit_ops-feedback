# CR-002 RollbackRequest.evidence_refs가 가리키는 지표를 읽는 방법

- 요청자: ops-feedback
- 대상: `RollbackRequest.evidence_refs`, `INTERFACES.md` I5/I7
- 처리 결과(헤드 회신): 결정: evidence_refs는 이 영역이 해석하는 불투명 Id, 게이트는 기록만. 지표 스냅샷 방식 유지.
- 이전 상태: 헤드 결정 필요

## 부족한 점
`evidence_refs`는 Id 배열이지만 그 Id가 무엇이고 어디서 읽는지 계약에 없다. ops-feedback은 롤백을 평가할 때 릴리스별 지표 스냅샷(`metric-<해시>`: 릴리스, 창, 총 턴 수, 실패 사례 수, 유형별 수, 실패율)을 만들어 자기 저장소에 두고 그 Id를 넣는다. 게이트나 사람이 이 Id를 해석하려면 읽는 경로가 필요하다.

## 제안
A. I7에 `get_metric(metric_id) -> dict`를 더하고 `MetricSnapshot` 스키마를 boundary-contracts에 둔다(MINOR 추가).
B. 또는 `evidence_refs`의 의미를 "FailureCase.case_id 목록도 허용"으로 정한다(이미 I7로 읽을 수 있다). ops-feedback은 대표 사례 Id를 함께 넣는 것으로 대응할 수 있다.

게이트가 근거를 해석하지 않고 기록만 한다면(I3 eval_report처럼) 현재 구현으로 충분하다. 이 경우 확인만 요청한다.

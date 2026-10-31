"""게이트웨이 노출 표(deploy-exposure.json, 이미지 라벨 io.openit.exposure)가 계약 Exposure를 지키고 실제 라우트와 맞는다(OPEN_GAPS U47).
정본은 리포 안 파일 deploy-exposure.json이고, 이 시험이 그것과 코드의 라우트가 어긋나지 않는지 본다."""
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from boundary_contracts import validator

from test_api_review import env, post  # noqa: F401  (검토 서버와 사례 하나가 있는 fixture)

ROOT = Path(__file__).resolve().parents[1]
EXPOSURE = json.loads((ROOT / "deploy-exposure.json").read_text(encoding="utf-8"))
# 이 서비스가 /v1 아래에서 실제로 내는 (메서드, 경로 모양). 새 라우트를 코드에 더하면 여기와 deploy-exposure.json에 함께 적어야 한다.
SERVED = {("GET", "/v1/cases"), ("GET", "/v1/cases/*"), ("GET", "/v1/deidentified-traces/*"), ("POST", "/v1/cases/*/review"), ("POST", "/v1/traces")}


def test_exposure_file_follows_the_contract():
    assert not list(validator("Exposure").iter_errors(EXPOSURE))
    assert EXPOSURE["service"] == "ops-feedback-ingest"


def _declared():
    out = {}
    for r in EXPOSURE["routes"]:
        for p in r["paths"]:
            for m in (r.get("methods") or ["GET", "POST"]):
                out[(m, p)] = r["auth"]
    return out


def test_every_served_route_is_declared_and_nothing_else_is():
    assert set(_declared()) == SERVED


def test_only_reviewer_accounts_get_the_case_surface_and_ingest_is_denied():
    d = _declared()
    assert d[("POST", "/v1/traces")]["kind"] == "denied"                  # 원본 수집 면은 게이트웨이 밖으로 열지 않는다
    for k in SERVED - {("POST", "/v1/traces")}:
        assert d[k] == {"kind": "account", "roles": ["reviewer"]}


def test_declared_exposed_routes_exist_in_the_service(env):
    base, cid = env
    def get(path):
        rq = urllib.request.Request(base + path, headers={"Authorization": "Bearer read-tok"})
        try:
            with urllib.request.urlopen(rq) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    st, case = get(f"/v1/cases/{cid}")
    assert st == 200
    assert get("/v1/cases")[0] == 200
    assert get(f"/v1/deidentified-traces/{case['deidentified_trace_ref']}")[0] == 200
    assert post(base, cid, {"review_status": "DISMISSED"})[0] == 201
    # 노출하지 않는 면: 읽기·검토 토큰으로 수집을 부르면 권한 없음(서비스가 면을 나눈다)
    rq = urllib.request.Request(base + "/v1/traces", method="POST", data=b"{}", headers={"Authorization": "Bearer read-tok"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(rq)
    assert e.value.code == 403


# ---- U427: 노출 선언의 시한(시간 사슬 D119, deploy ADR 0048)과 서버 쪽 시한 ----

def _timeouts():
    return [r["timeouts_ms"] for r in EXPOSURE["routes"] if "timeouts_ms" in r]


def test_declared_gateway_timeouts_for_reviewer_paths():
    """검토 경로(읽기 3 + 검토 게시 1)의 게이트웨이 상한: 연결 1초, 응답 머리글 10초. deploy ADR 0048이 정한 값이다."""
    assert _timeouts() == [{"dial": 1000, "response_header": 10000}] * 2


def test_server_side_request_deadline_is_shorter_than_the_gateway_response_header():
    """요청 전체 마감(D120, ADR 0028) < 게이트웨이 response_header: 서버가 계속 일하는 동안 화면이 504를 받는 일이 없다. 설정 값 비교(시간을 재지 않는다)."""
    from ops_feedback.wiring.ingest_service import REQUEST_DEADLINE_SECONDS
    assert REQUEST_DEADLINE_SECONDS * 1000 < min(t["response_header"] for t in _timeouts())
    assert REQUEST_DEADLINE_SECONDS == 9.0

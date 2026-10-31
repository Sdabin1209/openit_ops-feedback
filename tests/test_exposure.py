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


def _db_worst_case_ms():
    """설정 값으로만 계산한다(시간을 재지 않는다). 연결이 끊긴 호출 하나: 문장 상한+1초(소켓 abort 타이머) 뒤 재접속(접속 상한) 뒤 한 번 더 문장 상한+1초(PgBase._x, ADR 0008)."""
    from ops_feedback.wiring import pgbase
    stmt, conn = pgbase.DEFAULTS["OPS_DB_STATEMENT_TIMEOUT"], pgbase.DEFAULTS["OPS_DB_CONNECT_TIMEOUT"]
    return int(((stmt + 1) + conn + (stmt + 1)) * 1000)


@pytest.mark.xfail(strict=True, reason="U427 보고: 기본 DB 상한이면 연결이 끊긴 호출 하나의 최악이 11000ms로 게이트웨이 response_header 10000ms보다 길다. 헤드가 사슬을 다시 정하면 이 표시를 지운다")
def test_server_worst_case_for_one_db_call_is_shorter_than_the_gateway_response_header():
    assert _db_worst_case_ms() < max(t["response_header"] for t in _timeouts())


def test_db_worst_case_numbers_from_the_settings():
    assert _db_worst_case_ms() == 11000      # (3+1) + 3 + (3+1)초: 문장 상한 3, 접속 상한 3(pgbase.DEFAULTS, ADR 0008)

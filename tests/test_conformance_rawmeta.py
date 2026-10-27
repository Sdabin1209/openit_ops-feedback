"""원본 저장소의 처리 표시·집계·보존 삭제 적합성(메모리, SQLite, PostgreSQL 같은 시험)."""
from conftest import envelope, make_trace


def _send(log, n):
    for i in range(n):
        log.send(envelope(make_trace("s-1", f"t-{i}")))


def test_mark_unprocessed_reset(log_store):
    _send(log_store, 3)
    v = log_store.grant_deidentifier_view()
    assert [r["turn_id"] for r in v.read_unprocessed()] == ["t-0", "t-1", "t-2"]      # 수신 순서
    assert len(v.read_unprocessed(limit=2)) == 2
    log_store.mark("s-1", "t-0", "DONE", "cfg", "2026-10-15T00:00:00Z")
    log_store.mark("s-1", "t-1", "DEID_FAILED", "cfg", "2026-10-15T00:00:00Z")
    assert [r["turn_id"] for r in v.read_unprocessed()] == ["t-2"]
    assert log_store.reset_processing("DEID_FAILED") == 1
    assert [r["turn_id"] for r in v.read_unprocessed()] == ["t-1", "t-2"]
    assert log_store.reset_processing() == 1
    assert len(v.read_unprocessed()) == 3


def test_counts_by_release_and_purge(log_store):
    _send(log_store, 3)                                    # received_at: ...:01, :02, :03
    log_store.send(envelope(make_trace("s-2", "t-0"), release_id="rel-0009"))     # :04
    assert log_store.counts_by_release() == {"rel-0002": 3, "rel-0009": 1}
    assert log_store.counts_by_release(since="2026-10-14T00:00:03Z") == {"rel-0002": 1, "rel-0009": 1}
    assert log_store.purge_received_before("2026-10-14T00:00:03Z") == 2       # 엄격히 이전만 삭제
    assert len(log_store.grant_deidentifier_view().read_unprocessed()) == 2
    # 삭제된 키를 다시 보내면 새로 받는다(DUPLICATE가 아니다)
    assert log_store.send(envelope(make_trace("s-1", "t-0")))["status"] == "ACCEPTED"


def test_ping(log_store):
    log_store.ping()

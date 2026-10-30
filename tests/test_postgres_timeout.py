"""PostgreSQL 시한·연결 끊김에 대한 이 영역의 책임(U421). statement_timeout이 실제로 자르는가, libpq 접속 시한, docker pause 같은 일은 PostgreSQL·psycopg의 동작이라 시험하지 않는다.
여기서는 (1) 넘긴 값이 맞는가(연결 파라미터·설정 읽기) (2) 가짜 드라이버가 시간 초과·연결 끊김 오류를 즉시 던질 때 우리 코드가 맞게 처리하는가를 본다. 시계를 쓰지 않는다."""
import psycopg
import pytest
from boundary_contracts.errors import BoundaryError

from ops_feedback.wiring import pgbase


# ---- (1) 넘긴 값 ----

def test_connection_parameters_carry_statement_and_connect_timeouts_and_keep_search_path():
    p = pgbase._params("postgresql://u:pw@h:5432/db?options=-csearch_path%3Dt_x", 2.2, 1.5)
    assert p["connect_timeout"] == 3                                    # libpq는 정수 초: 올림, 최소 1
    assert "-c statement_timeout=1500" in p["options"] and "search_path=t_x" in p["options"]
    assert pgbase._params("postgresql://u:pw@h/db", 0.1, 0.0004)["connect_timeout"] == 1
    assert "statement_timeout=1" in pgbase._params("postgresql://u:pw@h/db", 1, 0.0004)["options"]      # 최소 1ms


def test_default_limits_and_env_override(monkeypatch):
    for k in pgbase.DEFAULTS:
        monkeypatch.delenv(k, raising=False)
    assert {k: pgbase._setting(k) for k in pgbase.DEFAULTS} == pgbase.DEFAULTS
    monkeypatch.setenv("OPS_DB_STATEMENT_TIMEOUT", "7")
    assert pgbase._setting("OPS_DB_STATEMENT_TIMEOUT") == 7.0


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "3601"])
def test_invalid_timeout_setting_is_rejected(monkeypatch, bad):
    monkeypatch.setenv("OPS_DB_STATEMENT_TIMEOUT", bad)
    with pytest.raises(BoundaryError):
        pgbase._setting("OPS_DB_STATEMENT_TIMEOUT")


# ---- (2) 가짜 드라이버가 즉시 오류를 던질 때 ----

class Conn:
    def __init__(self, fail_with=None):
        self.fail_with, self.closed, self.broken, self.calls = fail_with, False, False, 0

    def execute(self, *a):
        self.calls += 1
        if self.fail_with is not None:
            if type(self.fail_with) is psycopg.OperationalError:
                self.broken = True            # 끊긴 연결은 broken으로 보인다
            raise self.fail_with
        return self

    def fetchone(self):
        return (1,)

    def close(self):
        self.closed = True


def store(first, second=None, *, url="postgresql://svc:pw-canary-77@127.0.0.1:1/db"):
    """연결을 열 때마다 준비한 가짜 연결을 내는 PgBase(생성자 우회: 데이터베이스 없이)."""
    import threading
    b = pgbase.PgBase.__new__(pgbase.PgBase)
    b._url, b._lock, b.statement_timeout = url, threading.RLock(), 3.0
    b.probe_timeout = 2.0
    conns = [c for c in (first, second) if c is not None]
    b.opened = 0

    def _open():
        b.opened += 1
        return conns.pop(0)
    b._open = _open
    b._db = _open()
    return b


def test_lost_connection_is_reopened_and_the_call_retried_once():
    a, b2 = Conn(psycopg.OperationalError("server closed")), Conn()
    s = store(a, b2)
    assert s._x(lambda: s._db.execute("x")) is b2
    assert s.opened == 2 and a.calls == 1 and b2.calls == 1


def test_unrecoverable_loss_is_unavailable_and_hides_the_url():
    s = store(Conn(psycopg.OperationalError("down")), Conn(psycopg.OperationalError("still down")))
    with pytest.raises(BoundaryError) as e:
        s._x(lambda: s._db.execute("x"))
    assert e.value.code == "UNAVAILABLE" and e.value.retryable
    assert "pw-canary-77" not in e.value.message and "127.0.0.1" not in e.value.message and "OperationalError" in e.value.message


def test_statement_timeout_from_the_server_is_unavailable_and_not_retried():
    """서버가 statement_timeout으로 문장을 취소하면(QueryCanceled) 연결은 살아 있다: 다시 열지 않고 재시도 없이 UNAVAILABLE."""
    c = Conn(psycopg.errors.QueryCanceled("canceling statement due to statement timeout"))
    s = store(c)
    with pytest.raises(BoundaryError) as e:
        s._x(lambda: s._db.execute("x"))
    assert e.value.code == "UNAVAILABLE" and s.opened == 1 and c.calls == 1


def test_sql_errors_are_not_retried():
    c = Conn(psycopg.errors.UniqueViolation("dup"))
    s = store(c)
    with pytest.raises(BoundaryError):
        s._x(lambda: s._db.execute("x"))
    assert c.calls == 1 and s.opened == 1


def test_probe_failure_is_unavailable_with_class_name_only(monkeypatch):
    """/readyz의 DB 확인: 접속이 시간 초과 등으로 실패하면 UNAVAILABLE이고 접속 정보는 싣지 않는다."""
    def boom(**kw):
        raise psycopg.OperationalError("connection timeout expired host=127.0.0.1 password=pw-canary-77")
    monkeypatch.setattr(psycopg, "connect", boom)
    s = store(Conn())
    s.probe_timeout = 2.0
    with pytest.raises(BoundaryError) as e:
        s.ping()
    assert e.value.code == "UNAVAILABLE" and "pw-canary-77" not in e.value.message and "OperationalError" in e.value.message


def test_connect_failure_is_unavailable_with_class_name_only(monkeypatch):
    def boom(**kw):
        raise psycopg.OperationalError("timeout password=pw-canary-77")
    monkeypatch.setattr(psycopg, "connect", boom)
    with pytest.raises(BoundaryError) as e:
        pgbase._connect("postgresql://u:pw-canary-77@h/db", "로그 저장소", 3.0, 3.0)
    assert e.value.code == "UNAVAILABLE" and "pw-canary-77" not in e.value.message


# ---- 시계·타이머 주입(ADR 0027): 우리가 정한 마감이 맞게 걸리는지 값으로 본다 ----

class FakeTimer:
    made = []

    def __init__(self, secs, fn, args=()):
        self.secs, self.fn, self.args, self.started, self.cancelled = secs, fn, args, False, False
        FakeTimer.made.append(self)

    daemon = True

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


def test_each_call_arms_an_abort_timer_of_statement_timeout_plus_one_second_and_disarms_it():
    FakeTimer.made.clear()
    s = store(Conn())
    s.TIMER = FakeTimer
    s._x(lambda: 1)
    (t,) = FakeTimer.made
    assert t.secs == 4.0 and t.started and t.cancelled and t.fn is pgbase._abort      # statement_timeout 3초 + 1초(ADR 0008)


def test_probe_remaining_time_comes_from_the_injected_clock(monkeypatch):
    FakeTimer.made.clear()
    ticks = iter([100.0, 100.5])                      # 접속에 0.5초가 걸렸다고 시계가 말한다
    s = store(Conn())
    s.TIMER, s.CLOCK = FakeTimer, lambda: next(ticks)
    s.probe_timeout = 2.0
    monkeypatch.setattr(psycopg, "connect", lambda **kw: Conn())
    s.ping()
    (t,) = FakeTimer.made
    assert t.secs == 1.5 and t.started and t.cancelled      # 남은 시간 = probe 2.0 - 접속 0.5


def test_job_exits_with_code_2_and_prints_only_the_code_when_the_database_is_unavailable(tmp_path, monkeypatch, capsys):
    """DB 접속·문장이 시간 초과 등으로 UNAVAILABLE이면 잡은 스택 트레이스 없이 코드만 내고 종료 코드 2로 끝난다(가짜 의존이 즉시 던진다)."""
    import yaml
    import ops_feedback.job.__main__ as job
    from conftest import base_config
    cfg = tmp_path / "c.yaml"
    cfg.write_text(yaml.safe_dump(base_config()))

    def down(*a, **k):
        raise BoundaryError("UNAVAILABLE", "로그 저장소 접속 실패: OperationalError")
    monkeypatch.setattr(job, "build_from_config", down)
    assert job.main(["--config", str(cfg)]) == 2
    err = capsys.readouterr().err
    assert "UNAVAILABLE" in err and "Traceback" not in err

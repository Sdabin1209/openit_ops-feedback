"""PostgreSQL 공통(접속·스키마 멱등 생성·오류 변환). 원본 쪽 백엔드는 logstore.py, 실패 사례 쪽은 failurestore.py에 있어 저장소 분리가 소스에서도 유지된다. SQLite 백엔드와 같은 인터페이스·같은 의미이고 같은 적합성 시험을 통과한다.

접속 문자열(암호 포함 가능)은 이 모듈 밖으로 나가지 않는다. 오류·로그·/readyz에는 예외 클래스 이름만 쓴다.
스키마는 시작 시 멱등으로 만든다(advisory lock으로 서버와 잡이 동시에 떠도 안전). JSON은 TEXT로 저장해 SQLite와 바이트까지 같다.
"""
from __future__ import annotations

import os
import socket
import threading
import time

from boundary_contracts.errors import BoundaryError

_LOCK_KEY = 7264001   # 스키마 생성용 advisory lock 키(이 영역 고정값)

# 대기 상한(초). 환경 변수로 바꾼다(비밀이 아니므로 NAME_FILE 없이 값 그대로). 근거는 docs/boundary.md 장애 절:
# 이 영역의 문장은 인덱스 있는 단건 INSERT/SELECT와 잡의 한 배치 조회로 정상일 때 수 ms~수십 ms이므로 3초면 느린 디스크·잠깐의 잠금은 견딘다.
# 수집 호출자(survey-service outbox)는 UNAVAILABLE을 재시도하므로 오래 매달리지 않고 빨리 실패하는 편이 안전하다.
DEFAULTS = {"OPS_DB_CONNECT_TIMEOUT": 3.0,     # 접속 상한. libpq는 정수 초로 올림한다(최소 1)
            "OPS_DB_STATEMENT_TIMEOUT": 3.0,   # 문장 하나. 서버 쪽 statement_timeout + 클라이언트 쪽 강제 끊기(서버가 얼어도 끝난다)
            "OPS_DB_PROBE_TIMEOUT": 2.0,       # /readyz의 DB 확인 전체 상한
            "OPS_DB_DDL_TIMEOUT": 30.0}        # 시작 때 스키마 생성(다른 인스턴스의 advisory lock을 기다릴 수 있다)


def _setting(name: str) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return DEFAULTS[name]
    try:
        v = float(raw)
    except ValueError:
        v = 0.0
    if not (0 < v <= 3600):
        raise BoundaryError("INVALID_RECORD", f"{name}은 0보다 크고 3600 이하인 초 단위 숫자여야 한다")
    return v


def _params(url: str, connect_secs: float, stmt_secs: float) -> dict:
    """URL의 options(예: search_path)를 덮어쓰지 않고 statement_timeout을 이어 붙인다."""
    import psycopg
    params = psycopg.conninfo.conninfo_to_dict(url)
    params["options"] = (params.get("options", "") + f" -c statement_timeout={max(1, int(stmt_secs * 1000))}").strip()
    params["connect_timeout"] = max(1, int(connect_secs + 0.999))
    return params


def _abort(conn) -> None:
    """서버가 얼어 응답이 없을 때(docker pause 등) 서버 쪽 statement_timeout은 듣지 못한다. 소켓을 닫아 막힌 읽기를 끝낸다."""
    try:
        s = socket.socket(fileno=os.dup(conn.fileno()))
        try:
            s.shutdown(socket.SHUT_RDWR)
        finally:
            s.close()
    except Exception:
        pass


def _connect(url: str, what: str, connect_secs: float, stmt_secs: float):
    try:
        import psycopg
    except ImportError:   # 드라이버가 없으면 접속 문자열을 만지지 않고 알린다
        raise BoundaryError("UNAVAILABLE", f"{what}: psycopg 드라이버가 없다") from None
    try:
        return psycopg.connect(autocommit=True, **_params(url, connect_secs, stmt_secs))
    except Exception as e:     # 메시지에 host/user가 들어갈 수 있어 클래스 이름만 남긴다
        raise BoundaryError("UNAVAILABLE", f"{what} 접속 실패: {type(e).__name__}") from None


class PgBase:
    KIND = "postgresql"
    WHAT = "저장소"
    DDL = ""

    def __init__(self, url: str) -> None:
        self._url = url                      # 어디에도 출력하지 않는다
        self._lock = threading.RLock()
        self.connect_timeout = _setting("OPS_DB_CONNECT_TIMEOUT")
        self.statement_timeout = _setting("OPS_DB_STATEMENT_TIMEOUT")
        self.probe_timeout = _setting("OPS_DB_PROBE_TIMEOUT")
        ddl_timeout = _setting("OPS_DB_DDL_TIMEOUT")
        def ddl():
            self._db.execute(f"SET statement_timeout = {int(ddl_timeout * 1000)}")
            try:
                self._db.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
                try:
                    self._db.execute(self.DDL)
                finally:
                    self._db.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
            finally:
                self._db.execute(f"SET statement_timeout = {int(self.statement_timeout * 1000)}")
        self._db = self._open()
        self._x(ddl, limit=ddl_timeout)

    def _open(self):
        return _connect(self._url, self.WHAT, self.connect_timeout, self.statement_timeout)

    def _alive(self) -> bool:
        return not (self._db.closed or self._db.broken)

    def _x(self, fn, limit=None):
        """한 번의 호출. 연결이 끊긴 경우(서버 재시작, 연결 강제 종료)에만 연결을 다시 열고 같은 호출을 한 번 재시도한다.

        재시도의 근거: 모든 호출은 자동 커밋 단일 문장이거나(DDL은 전체가 멱등) 멱등 쓰기다. 끊긴 순간 서버가 이미 커밋했을 수 있는
        불확실한 쓰기도 재시도하면 같은 상태로 수렴한다(INSERT는 ON CONFLICT, UPDATE·DELETE는 같은 조건). 달라질 수 있는 것은
        반환 건수뿐이다(예: 이미 들어간 원본을 재시도에서 '이미 있음'으로 보아 DUPLICATE). 데이터는 한 건도 잃거나 늘지 않는다.
        SQL 오류(제약, 문법 등)는 재시도하지 않는다. 재접속도 실패하면 UNAVAILABLE(retryable)이다.

        시간 상한: 시도 하나는 statement_timeout + 1초 안에 끝난다(서버가 응답하지 못하면 클라이언트가 소켓을 닫는다).
        끊긴 연결로 재시도하면 접속 상한(connect_timeout)이 더해진다. 락(self._lock)은 이 시간만 쥔다.
        """
        import psycopg
        limit = (limit or self.statement_timeout) + 1.0
        for attempt in (1, 2):
            timer = None
            try:
                with self._lock:
                    if not self._alive():
                        self._db = self._open()
                    timer = threading.Timer(limit, _abort, args=(self._db,))
                    timer.daemon = True
                    timer.start()
                    return fn()
            except psycopg.Error as e:
                lost = isinstance(e, (psycopg.OperationalError, psycopg.InterfaceError)) and not self._alive()
                if lost and attempt == 1:
                    continue
                raise BoundaryError("UNAVAILABLE", f"{self.WHAT} 오류: {type(e).__name__}") from None
            finally:
                if timer is not None:
                    timer.cancel()

    def ping(self):
        """/readyz용 확인. **주 연결과 self._lock을 쓰지 않는다**: 다른 요청이 연결을 쥐고 기다리는 중이어도 막히지 않고,
        새 연결로 SELECT 1을 해서 probe_timeout(접속 + 문장 합계) 안에 끝난다. 접속 단계는 libpq connect_timeout이,
        접속 뒤 문장은 남은 시간 뒤에 소켓을 닫는 타이머가 끊는다. 실패하면 UNAVAILABLE(예외 클래스 이름만)."""
        import psycopg
        conn = None
        timer = None
        t0 = time.monotonic()
        try:
            conn = psycopg.connect(autocommit=True, **_params(self._url, self.probe_timeout, self.probe_timeout))
            remain = max(0.05, self.probe_timeout - (time.monotonic() - t0))
            timer = threading.Timer(remain, _abort, args=(conn,))
            timer.daemon = True
            timer.start()
            conn.execute("SELECT 1").fetchone()
        except Exception as e:
            raise BoundaryError("UNAVAILABLE", f"{self.WHAT} 확인 실패: {type(e).__name__}") from None
        finally:
            if timer is not None:
                timer.cancel()
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def close(self) -> None:
        self._db.close()

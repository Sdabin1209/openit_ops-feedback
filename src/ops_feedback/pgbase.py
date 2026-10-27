"""PostgreSQL 공통(접속·스키마 멱등 생성·오류 변환). 원본 쪽 백엔드는 logstore.py, 실패 사례 쪽은 failurestore.py에 있어 저장소 분리가 소스에서도 유지된다. SQLite 백엔드와 같은 인터페이스·같은 의미이고 같은 적합성 시험을 통과한다.

접속 문자열(암호 포함 가능)은 이 모듈 밖으로 나가지 않는다. 오류·로그·/readyz에는 예외 클래스 이름만 쓴다.
스키마는 시작 시 멱등으로 만든다(advisory lock으로 서버와 잡이 동시에 떠도 안전). JSON은 TEXT로 저장해 SQLite와 바이트까지 같다.
"""
from __future__ import annotations

import threading

from boundary_contracts.errors import BoundaryError

_LOCK_KEY = 7264001   # 스키마 생성용 advisory lock 키(이 영역 고정값)


def _connect(url: str, what: str):
    try:
        import psycopg
    except ImportError as e:   # 드라이버가 없으면 접속 문자열을 만지지 않고 알린다
        raise BoundaryError("UNAVAILABLE", f"{what}: psycopg 드라이버가 없다") from None
    try:
        return psycopg.connect(url, autocommit=True, connect_timeout=10)
    except Exception as e:     # 메시지에 host/user가 들어갈 수 있어 클래스 이름만 남긴다
        raise BoundaryError("UNAVAILABLE", f"{what} 접속 실패: {type(e).__name__}") from None


class PgBase:
    KIND = "postgresql"
    WHAT = "저장소"
    DDL = ""

    def __init__(self, url: str) -> None:
        self._url = url                      # 어디에도 출력하지 않는다
        self._lock = threading.RLock()
        self._db = _connect(url, self.WHAT)
        def ddl():
            self._db.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
            try:
                self._db.execute(self.DDL)
            finally:
                self._db.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
        self._x(ddl)

    def _alive(self) -> bool:
        return not (self._db.closed or self._db.broken)

    def _x(self, fn):
        """한 번의 호출. 연결이 끊긴 경우(서버 재시작, 연결 강제 종료)에만 연결을 다시 열고 같은 호출을 한 번 재시도한다.

        재시도의 근거: 모든 호출은 자동 커밋 단일 문장이거나(DDL은 전체가 멱등) 멱등 쓰기다. 끊긴 순간 서버가 이미 커밋했을 수 있는
        불확실한 쓰기도 재시도하면 같은 상태로 수렴한다(INSERT는 ON CONFLICT, UPDATE·DELETE는 같은 조건). 달라질 수 있는 것은
        반환 건수뿐이다(예: 이미 들어간 원본을 재시도에서 '이미 있음'으로 보아 DUPLICATE). 데이터는 한 건도 잃거나 늘지 않는다.
        SQL 오류(제약, 문법 등)는 재시도하지 않는다. 재접속도 실패하면 UNAVAILABLE(retryable)이다.
        """
        import psycopg
        for attempt in (1, 2):
            try:
                with self._lock:
                    if not self._alive():
                        self._db = _connect(self._url, self.WHAT)
                    return fn()
            except psycopg.Error as e:
                lost = isinstance(e, (psycopg.OperationalError, psycopg.InterfaceError)) and not self._alive()
                if lost and attempt == 1:
                    continue
                raise BoundaryError("UNAVAILABLE", f"{self.WHAT} 오류: {type(e).__name__}") from None

    def ping(self):
        self._x(lambda: self._db.execute("SELECT 1").fetchone())

    def close(self) -> None:
        self._db.close()

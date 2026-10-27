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
        self._lock = threading.RLock()
        self._db = _connect(url, self.WHAT)
        def ddl():
            self._db.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
            try:
                self._db.execute(self.DDL)
            finally:
                self._db.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
        self._x(ddl)

    def _x(self, fn):
        import psycopg
        try:
            with self._lock:
                return fn()
        except psycopg.Error as e:
            raise BoundaryError("UNAVAILABLE", f"{self.WHAT} 오류: {type(e).__name__}") from None

    def ping(self):
        self._x(lambda: self._db.execute("SELECT 1").fetchone())

    def close(self) -> None:
        self._db.close()

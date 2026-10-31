"""사람용 요청의 서버 쪽 전체 마감(D120, ADR 0028). 요청이 시작될 때 마감 시각을 주입한 시계로 정하고, 각 단계(DB 문장 등)는 남은 시간을 상한으로 쓴다.
요청 범위(contextvar)에 둔다: 저장소·조립 코드가 마감을 인자로 끌고 다니지 않아도 되고 요청마다 따로다. 시계는 주입한다(U422/U423): 이 모듈은 time을 부르지 않는다."""
from __future__ import annotations

import contextlib
import contextvars
from typing import Callable, Optional

from boundary_contracts.errors import BoundaryError


class Deadline:
    def __init__(self, clock: Callable[[], float], seconds: float) -> None:
        self._clock = clock
        self._at = clock() + seconds

    def remaining(self) -> float:
        return self._at - self._clock()

    def check(self) -> None:
        """마감을 넘었으면 더 진행하지 않는다(503, 재시도 가능). 쓰기는 마지막 한 문장이라 그 전에 멈추면 반쯤 쓴 상태가 없다."""
        if self.remaining() <= 0:
            raise BoundaryError("UNAVAILABLE", "요청 마감을 넘었다")


_current: contextvars.ContextVar[Optional[Deadline]] = contextvars.ContextVar("ops_request_deadline", default=None)


@contextlib.contextmanager
def scope(deadline: Deadline):
    token = _current.set(deadline)
    try:
        yield deadline
    finally:
        _current.reset(token)


def check_current() -> None:
    d = _current.get()
    if d is not None:
        d.check()


def remaining_current() -> Optional[float]:
    d = _current.get()
    return None if d is None else d.remaining()

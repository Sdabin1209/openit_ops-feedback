"""이웃 release-gate의 릴리스 정보 읽기(I4). 보내는 쪽 통신 컴포넌트: 계약 클라이언트(boundary_contracts.sdk.ReleaseReader 모양의 객체)를 받아 영역 안의 값으로 바꿔 준다.
업무 컴포넌트는 계약 클라이언트나 릴리스 응답 모양을 모른다."""
from __future__ import annotations

from typing import Iterator, Optional

from boundary_contracts.errors import BoundaryError


class ReleaseLink:
    """한 번의 잡 실행에 하나. 문항 세트 조회 결과를 이번 실행 동안 기억한다."""

    def __init__(self, reader) -> None:
        self._reader = reader
        self._qs_cache: dict = {}

    def question_set_of(self, release_id: str, question_set_id: Optional[str] = None) -> Optional[dict]:
        """source_release_id의 ReleaseManifest에서 이 세션이 쓴 문항 세트 ref(ArtifactRef: id·version·content_hash). 읽을 수 없으면 None(필드 생략, 1.0 호환).
        3.3.0: 매니페스트는 문진표 목록(question_sets)이다. TraceEnvelope.question_set_id 로 고르고, 없으면 목록이 하나일 때만 그것을 쓴다(둘 이상이면 None: 지어내지 않는다).

        한 번 UNAVAILABLE(시간 초과 포함)이면 이번 실행에서는 다른 릴리스도 부르지 않는다: 상류가 멈춘 채 릴리스마다 읽기 timeout을 기다리지 않게
        한다(원칙 4). 알려진 위험: 일시 장애 때 만든 사례는 문항 세트 없이 게시되고, version 1은 나중에 고쳐 쓰지 않는다(docs/boundary.md)."""
        cache = self._qs_cache
        if "__down__" in cache:
            return None
        key = (release_id, question_set_id)
        if key not in cache:
            try:
                sets = self._reader.get_release(release_id).get("question_sets") or []
                if question_set_id is not None:
                    sets = [x for x in sets if x.get("id") == question_set_id]
                qs = sets[0] if len(sets) == 1 else None
                cache[key] = {k: qs[k] for k in ("id", "version", "content_hash")} if isinstance(qs, dict) else None
            except BoundaryError as e:
                if e.retryable:
                    cache["__down__"] = e.code
                cache[key] = None
            except (AttributeError, TypeError, KeyError, ValueError):     # 깨진 응답은 지어내지 않고 생략한다(원칙 10)
                cache[key] = None
        return cache[key]

    def active_pointer(self) -> Optional[dict]:
        return self._reader.get_active_pointer()

    def release_id_pages(self) -> Iterator[list]:
        """릴리스 ID를 최신부터 한 쪽씩."""
        cursor = None
        while True:
            items, cursor = self._reader.list_releases(cursor=cursor, limit=100)
            yield [m["release_id"] for m in items]
            if cursor is None:
                return

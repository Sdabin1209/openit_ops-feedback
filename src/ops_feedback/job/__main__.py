"""주기 잡 진입점: 한 번 실행하고 끝난다(D39). 명령: ops-feedback-job --config config/job.example.yaml [--limit N] [--reprocess] [--retry-deid-failed] [--no-rollback]
인자 읽기·종료 코드·출력만 한다(업무 규칙 없음). 조립은 wiring/에 있다."""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from boundary_contracts import validator
from boundary_contracts.errors import BoundaryError
from boundary_contracts.secrets import SecretError

from ..config import load_config
from ..wiring.build import build_from_config
from ..wiring.pipeline import run_once

_JOB_RESULT = validator("OpsFeedbackJobResult")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ops-feedback 주기 잡 한 번 실행")
    ap.add_argument("--config", required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--reprocess", action="store_true", help="처리 표시를 모두 지우고 다시 처리(결과는 같다)")
    ap.add_argument("--retry-deid-failed", action="store_true")
    ap.add_argument("--no-rollback", action="store_true")
    a = ap.parse_args(argv)
    try:
        cfg = load_config(a.config)
    except ValueError as e:
        # 설정 오류는 스택 트레이스 없이 이유만 내고 종료 코드 2(접속 실패 등과 같은 "실행 못 함" 코드)
        print(f"오류: {e}", file=sys.stderr)
        return 2
    # DB가 무응답이면 문장·접속 상한(OPS_DB_*_TIMEOUT) 안에 BoundaryError(UNAVAILABLE)로 끝난다. 무한 대기하지 않고 종료 코드 2로 끝낸다(스택 트레이스 없이 코드만).
    try:
        log, store, deid, pseudonymize, detector, classifier, planner, reader = build_from_config(cfg, dict(os.environ))
        rep = run_once(cfg=cfg, log=log, store=store, deid=deid, pseudonymize=pseudonymize, detector=detector, classifier=classifier, planner=planner,
                       limit=a.limit, reprocess=a.reprocess, retry_deid_failed=a.retry_deid_failed,
                       evaluate_rollback=not a.no_rollback, release_reader=reader)
    except BoundaryError as e:
        print(json.dumps({"error": e.code, "message": e.message}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (ValueError, SecretError) as e:
        # 비밀이 없거나 짧음·설정 값 오류는 스택 트레이스 없이 이유만 낸다(메시지에는 이름·길이 요구만 있고 값은 없다). 종료 코드 2(실행 못 함)
        print(f"오류: {e}", file=sys.stderr)
        return 2
    for w in rep.warnings:
        print("경고: " + w, file=sys.stderr)
    result = rep.as_dict()
    bad = list(_JOB_RESULT.iter_errors(result))
    if bad:
        # 자기 검사: 스케줄러가 읽는 stdout 한 줄이 계약(OpsFeedbackJobResult)을 어기면 내보내지 않는다. 위반 값은 개인정보일 수 있어 경로와 규칙만 낸다.
        print("오류: 잡 결과가 OpsFeedbackJobResult를 어긴다: " + "; ".join(f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.validator}" for e in bad[:3]), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())

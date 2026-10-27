"""관리 도구(삭제·최소화·스키마). 서버 API가 아니라 명령줄이다: DB 접속 비밀과 salt를 파일로 가진 사람만 쓸 수 있다(인증 = 비밀 소유).

  python -m ops_feedback.admin delete-session --session-id S [--dry-run]
      세션 하나의 원본, 그 세션의 비식별 trace와 사례(모든 version)를 지운다. 비식별 trace의 session_id는 salt 해시라 salt가 필요하다
      (OPS_DEID_SALT_FILE, 잡과 같은 salt). 지표·롤백 요청 기록(aux)은 세션 단위 정보가 없어 지우지 않는다.
  python -m ops_feedback.admin purge --raw-days N [--metrics-days K] [--cases-days M [--include-confirmed]] [--include-unprocessed]
      원본(기본은 처리 표시가 있는 것만)과 오래된 사례·고아 trace를 지운다. 기간 값은 사용자가 정하며 기본값이 없다.
  python -m ops_feedback.admin minimize-raw [--dry-run]
      처리가 끝난 원본의 봉투 내용을 지운다(중복 판별 키·해시·시각·릴리스는 남긴다).
  python -m ops_feedback.admin migrate
      스키마를 만든다. 서버·잡을 OPS_DB_SCHEMA=verify로 띄우면 앱 계정에 DDL 권한이 필요 없다(docs/runbook.md).

저장소는 서버·잡과 같은 환경 변수(OPS_LOG_DATABASE_URL[_FILE], OPS_FAILURE_DATABASE_URL[_FILE], 없으면 --log-db/--failure-db SQLite 경로)로 고른다.
종료 코드: 0 성공, 2 사용 오류·접속 실패, 3 지울 것을 찾지 못함(delete-session; salt·정책 확인). 출력은 건수만이며 세션 ID·내용은 출력하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import timedelta

from boundary_contracts import read_secret
from boundary_contracts.canonical import sha256_hash
from boundary_contracts.errors import BoundaryError

from .clock import fmt_ts, parse_ts, utc_now
from .storage import open_failure_store, open_log_store


def _open(a, env):
    if not (read_secret("OPS_LOG_DATABASE_URL", environ=env) or a.log_db):
        raise BoundaryError("INVALID_RECORD", "로그 저장소를 정해야 한다(OPS_LOG_DATABASE_URL[_FILE] 또는 --log-db)")
    if not (read_secret("OPS_FAILURE_DATABASE_URL", environ=env) or a.failure_db):
        raise BoundaryError("INVALID_RECORD", "실패 사례 저장소를 정해야 한다(OPS_FAILURE_DATABASE_URL[_FILE] 또는 --failure-db)")
    return open_log_store(env, a.log_db), open_failure_store(env, a.failure_db)


def session_pseudonym(salt: str, session_id: str) -> str:
    """비식별 처리기가 session_id 키에 붙이는 의사 식별자(deid.py `_string`과 같은 식)."""
    return "p-" + sha256_hash({"salt": salt, "key": "session_id", "value": session_id})[7:39]


def delete_session(log, fail, salt: str, session_id: str, dry_run: bool) -> dict:
    out = {"raw_rows": 0, "cases": 0, "traces": 0, "dry_run": dry_run}
    # 비식별 정책에 session_id 의사화가 없으면 trace에 원래 session_id가 남아 있다: 두 값을 모두 찾는다
    refs = sorted(set(fail.traces_of_session(session_pseudonym(salt, session_id)) + fail.traces_of_session(session_id))) if fail is not None else []
    if dry_run:
        out["traces"] = len(refs)
        return out
    out["raw_rows"] = log.delete_session(session_id)
    if fail is not None and refs:
        r = fail.delete_by_trace_refs(refs)
        out.update(cases=r["cases"], traces=r["traces"])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ops_feedback.admin", description=__doc__.split("\n")[0])
    ap.add_argument("--log-db")
    ap.add_argument("--failure-db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("delete-session")
    d.add_argument("--session-id", required=True)
    d.add_argument("--dry-run", action="store_true")
    pg = sub.add_parser("purge")
    pg.add_argument("--raw-days", type=float)
    pg.add_argument("--cases-days", type=float)
    pg.add_argument("--metrics-days", type=float)
    pg.add_argument("--include-confirmed", action="store_true")
    pg.add_argument("--include-unprocessed", action="store_true")
    m = sub.add_parser("minimize-raw")
    m.add_argument("--dry-run", action="store_true")
    sub.add_parser("migrate")
    a = ap.parse_args(argv)
    env = dict(os.environ)
    try:
        if a.cmd == "migrate":
            env["OPS_DB_SCHEMA"] = "apply"
            log, fail = _open(a, env)
            print(json.dumps({"migrated": True, "log_store": log.backend_kind(), "failure_store": fail.backend_kind()}))
            return 0
        log, fail = _open(a, env)
        if a.cmd == "delete-session":
            salt = read_secret("OPS_DEID_SALT", environ=env)
            if not salt:
                print("오류: OPS_DEID_SALT_FILE(또는 OPS_DEID_SALT)이 필요하다", file=sys.stderr)
                return 2
            out = delete_session(log, fail, salt, a.session_id, a.dry_run)
            found = out["raw_rows"] + out["traces"] + out["cases"]
            if not a.dry_run and found == 0 or a.dry_run and out["traces"] == 0:
                # 오류 없이 0건이면 잘못된 salt·정책(session_id 의사화 키가 다름)·이미 지워짐을 구별할 수 없으므로 종료 코드로 알린다
                out["note"] = "NOTHING_FOUND: 해당 세션의 비식별 trace를 찾지 못했다. salt와 비식별 정책(pseudonymize_keys의 session_id)을 확인한다"
                print(json.dumps(out, ensure_ascii=False, sort_keys=True))
                return 3
            print(json.dumps(out, sort_keys=True))
            return 0
        if a.cmd == "purge":
            if a.raw_days is None and a.cases_days is None and a.metrics_days is None:
                print("오류: --raw-days 또는 --cases-days가 필요하다(기간은 사용자가 정한다)", file=sys.stderr)
                return 2
            out = {}
            now = parse_ts(utc_now())
            if a.raw_days is not None:
                out["raw"] = log.purge_received_before(fmt_ts(now - timedelta(days=a.raw_days)), processed_only=not a.include_unprocessed)
            if a.cases_days is not None and fail is not None:
                out["cases"] = fail.purge_cases_created_before(fmt_ts(now - timedelta(days=a.cases_days)), include_confirmed=a.include_confirmed)
            if a.metrics_days is not None and fail is not None:
                out["aux"] = fail.purge_aux_before(fmt_ts(now - timedelta(days=a.metrics_days)))
            print(json.dumps(out, sort_keys=True))
            return 0
        if a.cmd == "minimize-raw":
            if a.dry_run:
                print(json.dumps({"dry_run": True}))
                return 0
            print(json.dumps({"minimized": log.minimize_processed()}))
            return 0
    except BoundaryError as e:
        print(json.dumps({"error": e.code, "message": e.message}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())

-- 로그 저장소(원본) 첫 스키마. 단위 3까지 시작 때 직접 실행하던 DDL과 같다: 이미 그 DDL로 만든 DB에서도 그대로 적용된다(IF NOT EXISTS).
CREATE TABLE IF NOT EXISTS raw_traces(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL, turn_id TEXT NOT NULL, release_id TEXT NOT NULL,
  received_at TEXT NOT NULL, envelope TEXT NOT NULL, envelope_hash TEXT NOT NULL,
  status TEXT, config_hash TEXT, processed_at TEXT,
  UNIQUE(session_id, turn_id));
CREATE INDEX IF NOT EXISTS raw_status ON raw_traces(status, seq);
CREATE INDEX IF NOT EXISTS raw_release ON raw_traces(release_id, received_at);

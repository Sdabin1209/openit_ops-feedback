-- 실패 사례 저장소 첫 스키마. 단위 3까지 시작 때 직접 실행하던 DDL과 같다(IF NOT EXISTS).
CREATE TABLE IF NOT EXISTS cases(
  seq BIGSERIAL PRIMARY KEY, case_id TEXT NOT NULL, version INTEGER NOT NULL,
  review_status TEXT NOT NULL, failure_type TEXT NOT NULL, source_release_id TEXT NOT NULL,
  created_at TEXT NOT NULL, record TEXT NOT NULL, UNIQUE(case_id, version));
CREATE INDEX IF NOT EXISTS cases_filter ON cases(review_status, failure_type, source_release_id);
CREATE INDEX IF NOT EXISTS cases_release ON cases(source_release_id, created_at);
CREATE TABLE IF NOT EXISTS traces(ref TEXT PRIMARY KEY, trace TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS aux(kind TEXT NOT NULL, key TEXT NOT NULL, record TEXT NOT NULL, PRIMARY KEY(kind, key));

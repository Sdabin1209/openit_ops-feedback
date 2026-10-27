.PHONY: test test-pg smoke
test:            ## PostgreSQL 없이(PostgreSQL 시험은 SKIP으로 표시된다)
	.venv/bin/python -m pytest -q
test-pg:         ## 일회용 PostgreSQL 컨테이너 위에서 전체 시험(건너뛰는 것 없음)
	scripts/dev-postgres.sh
smoke:           ## 이미지 빌드, 기동, 수집
	scripts/smoke.sh

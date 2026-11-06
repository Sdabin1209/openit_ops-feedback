"""Public input-declaration paths must not be mistaken for secret contents."""
import os
from pathlib import Path
import subprocess
import tempfile

import pytest
from waiting import HANG_GUARD_SECONDS

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("leak", [None, "inspect", "env"])
def test_smoke_secret_detection_uses_secret_contents_and_consumes_docker_output(leak):
    source = (ROOT / "scripts/smoke.sh").read_text()
    start = source.index('inspection=$(')
    end = source.index('PORT=$(', start)
    actual_checks = source[start:end]
    token = "8f" * 32
    public_paths = '/run/secrets/qa-read-tokens /run/secrets/ops-read-tokens /run/secrets/ops-review-tokens'
    # Large trailing output also detects grep -q/SIGPIPE false negatives.
    payload = public_paths + ('\n' + token if leak == 'inspect' else '') + '\n' + 'padding\n' * 20000
    with tempfile.TemporaryDirectory() as directory:
        fixture = Path(directory) / 'inspect.txt'
        fixture.write_text(payload)
        shell = r'''set -euo pipefail
fake_docker() {
  if [ "$1" = inspect ]; then cat "$FIXTURE";
  elif [ "$1" = exec ]; then printf '%s\n' "$ENV_FIXTURE";
  else return 2; fi
}
DOCKER=fake_docker; NAME=test
TOKEN=$MARKER; READTOKEN=$MARKER; REVTOKEN=$MARKER
INGEST_TOKEN=$MARKER; READ_TOKEN=$MARKER; REVIEW_TOKEN=$MARKER; DEID_SALT=$MARKER
''' + actual_checks
        result = subprocess.run(
            ['bash', '-c', shell], capture_output=True, text=True, timeout=HANG_GUARD_SECONDS,
            env={**os.environ, 'FIXTURE': str(fixture), 'MARKER': token,
                 'ENV_FIXTURE': token if leak == 'env' else 'TOKEN_FILE=/run/secrets/qa-read-tokens'},
        )
        assert result.returncode == (0 if leak is None else 1), result.stderr

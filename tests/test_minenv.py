from __future__ import annotations

import pytest

from minenv import clean_env

DOCKER_NAMES = ("DOCKER_CONFIG", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "SSH_AUTH_SOCK")


def test_docker_connection_names_pass_through_and_others_do_not(monkeypatch):
    for n in DOCKER_NAMES:
        monkeypatch.setenv(n, "fake-" + n)          # 가짜 값(출력하지 않는다)
    monkeypatch.setenv("OPS_INGEST_TOKEN", "fake")
    monkeypatch.setenv("UNLISTED_NAME", "fake")
    env = clean_env()
    assert all(env[n] == "fake-" + n for n in DOCKER_NAMES)
    assert "OPS_INGEST_TOKEN" not in env and "UNLISTED_NAME" not in env

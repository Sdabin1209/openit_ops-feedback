"""U99: 바깥 호출은 주소를 설정으로 받고, 인증서 검증을 끄지 않으며, SSL_CERT_FILE로 사설 루트를 믿고, DB 접속 문자열의 sslmode를 덮어쓰지 않는다."""
from __future__ import annotations

import re
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from boundary_contracts.clients import HttpReleaseReader
from boundary_contracts.errors import BoundaryError

SRC = Path(__file__).resolve().parent.parent / "src" / "ops_feedback"


def _code_files():
    return [p for p in SRC.rglob("*.py") if "types" not in p.parts]


def test_no_fixed_address_in_code():
    # 서비스 주소는 잡 설정(base_url)으로 들어온다. 코드에는 http:// 도, 서비스 이름:포트도 없다
    for p in _code_files():
        t = p.read_text("utf-8")
        assert not re.search(r"https?://", t), f"{p.name}: 고정 URL"
        assert not re.search(r"release-gate:\d+|ingest-api:\d+", t), f"{p.name}: 서비스 이름:포트"


def test_no_code_disables_certificate_verification():
    bad = re.compile(r"verify\s*=\s*False|CERT_NONE|check_hostname\s*=\s*False|_create_unverified_context|_create_default_https_context\s*=")
    for p in _code_files():
        assert not bad.search(p.read_text("utf-8")), f"{p.name}: 인증서 검증을 끈다"


def test_sslmode_from_connection_string_is_kept():
    from ops_feedback.migrate import _pg_uri
    from ops_feedback.wiring.pgbase import _params
    url = "postgresql://u:p@db.example/d?sslmode=verify-full&sslrootcert=/etc/root.pem&options=-csearch_path%3Ds"
    p = _params(url, 3.0, 3.0)
    assert p["sslmode"] == "verify-full" and p["sslrootcert"] == "/etc/root.pem"
    assert "search_path" in p["options"] and "statement_timeout" in p["options"]
    u = _pg_uri(url, 3.0, 30.0)
    assert "sslmode=verify-full" in u and "sslrootcert=%2Fetc%2Froot.pem" in u


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl 없음")
def test_https_call_trusts_private_root_via_ssl_cert_file(tmp_path, monkeypatch):
    d = tmp_path
    def sh(*a):
        subprocess.run(a, cwd=d, check=True, capture_output=True, timeout=30)
    sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.pem", "-days", "1", "-subj", "/CN=test-root")
    sh("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "srv.key", "-out", "srv.csr", "-subj", "/CN=localhost")
    (d / "ext.cnf").write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\n")
    sh("openssl", "x509", "-req", "-in", "srv.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial", "-out", "srv.pem", "-days", "1", "-extfile", "ext.cnf")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            raw = b'{"pipeline_schema_version": "x", "code": "NOT_FOUND", "message": "x", "retryable": false}'
            self.send_response(404)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(d / "srv.pem", d / "srv.key")
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"https://localhost:{srv.server_address[1]}"
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        with pytest.raises(BoundaryError) as e:                      # 루트를 모르면 검증이 실패한다(검증이 켜져 있다)
            HttpReleaseReader(url, "t").get_release("rel-1")
        assert e.value.code == "UNAVAILABLE"
        monkeypatch.setenv("SSL_CERT_FILE", str(d / "ca.pem"))      # 표준 환경 변수 하나로 사설 루트를 믿는다
        with pytest.raises(BoundaryError) as e:
            HttpReleaseReader(url, "t").get_release("rel-1")
        assert e.value.code == "NOT_FOUND"                          # TLS 연결과 검증이 통과해 서버의 응답까지 왔다
    finally:
        srv.shutdown()

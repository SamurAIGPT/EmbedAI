"""CA selection and verified HTTPS regression tests using a local test CA."""

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
import urllib3

from test_dev import dev_env


class CertificateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="open-dots-tls-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.ca_file = cls.root / "system-ca.pem"
        cls.server_cert = cls.root / "server.pem"
        cls.server_key = cls.root / "server-key.pem"
        cls.empty_ca_dir = cls.root / "empty-ca-directory"
        cls.empty_ca_dir.mkdir()
        now = datetime.now(timezone.utc)
        ca_key = ec.generate_private_key(ec.SECP256R1())
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Bootstrap test CA")])
        ca = (
            x509.CertificateBuilder()
            .subject_name(ca_name).issuer_name(ca_name)
            .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, content_commitment=False,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=None, decipher_only=None,
            ), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        key = ec.generate_private_key(ec.SECP256R1())
        leaf = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
            .issuer_name(ca_name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.KeyUsage(
                digital_signature=True, content_commitment=False,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=None, decipher_only=None,
            ), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        cls.ca_file.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        cls.server_cert.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
        cls.server_key.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))

    def setUp(self):
        env_patch = patch.dict(os.environ, {}, clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def test_missing_default_ca_uses_system_bundle(self):
        with patch.object(dev_env.ssl, "get_default_verify_paths", return_value=SimpleNamespace(cafile=None)), \
             patch.object(dev_env, "SYSTEM_CA_BUNDLES", (str(self.root / "missing.pem"), str(self.ca_file))):
            self.assertEqual(dev_env.select_ca_bundle({}), str(self.ca_file))

    def test_valid_openssl_default_takes_precedence(self):
        with patch.object(dev_env.ssl, "get_default_verify_paths", return_value=SimpleNamespace(cafile=str(self.ca_file))):
            self.assertEqual(dev_env.select_ca_bundle({}), str(self.ca_file))

    def test_no_system_bundle_falls_back_to_certifi(self):
        with patch.object(dev_env.ssl, "get_default_verify_paths", return_value=SimpleNamespace(cafile=None)), \
             patch.object(dev_env, "SYSTEM_CA_BUNDLES", ()):
            selected = dev_env.select_ca_bundle({})
            self.assertEqual(selected, dev_env.certifi.where())
            self.assertGreater(len(ssl.create_default_context(cafile=selected).get_ca_certs()), 0)

    def test_explicit_ca_from_dotenv_and_environment_is_preserved(self):
        configured = {"SSL_CERT_FILE": str(self.ca_file)}
        self.assertEqual(dev_env.select_ca_bundle(configured), str(self.ca_file))
        with patch.dict(os.environ, configured):
            self.assertEqual(dev_env.select_ca_bundle({"SSL_CERT_FILE": "ignored.pem"}), str(self.ca_file))

    def test_invalid_explicit_ca_fails_without_falling_back(self):
        malformed = self.root / "malformed-ca.pem"
        malformed.write_text("not a certificate")
        for path in (str(malformed), str(self.root / "missing.pem")):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "Set SSL_CERT_FILE"):
                    dev_env.select_ca_bundle({"SSL_CERT_FILE": path})

    def test_exported_system_bundle_fixes_verified_https_and_rejects_wrong_host(self):
        class Handler(BaseHTTPRequestHandler):
            def handle(self):
                try:
                    super().handle()
                except (ConnectionResetError, BrokenPipeError):
                    # A client rejecting the hostname closes the TLS socket.
                    pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"healthy")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.server_cert, self.server_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"https://localhost:{server.server_port}/health"
        # Same default CA behavior as the Boat SDK's urllib3 transport.
        pool_args = dict(
            cert_reqs=ssl.CERT_REQUIRED, ca_certs=None, retries=False,
            timeout=urllib3.Timeout(connect=2, read=2),
        )
        with patch.dict(os.environ, {"SSL_CERT_DIR": str(self.empty_ca_dir)}):
            untrusted = urllib3.PoolManager(**pool_args)
            self.addCleanup(untrusted.clear)
            with self.assertRaises(urllib3.exceptions.SSLError):
                untrusted.request("GET", url)

            with patch.object(dev_env.ssl, "get_default_verify_paths", return_value=SimpleNamespace(cafile=None)), \
                 patch.object(dev_env, "SYSTEM_CA_BUNDLES", (str(self.ca_file),)):
                selected = dev_env.select_ca_bundle({})
            with patch.dict(os.environ, {"SSL_CERT_FILE": selected}):
                dev_env.check_boat_tls(url)
                trusted = urllib3.PoolManager(**pool_args)
                self.addCleanup(trusted.clear)
                response = trusted.request("GET", url)
                self.assertEqual((response.status, response.data), (200, b"healthy"))
                wrong_host = urllib3.PoolManager(assert_hostname="wrong.example", **pool_args)
                self.addCleanup(wrong_host.clear)
                with self.assertRaises(urllib3.exceptions.SSLError):
                    wrong_host.request("GET", url)

    def test_tls_preflight_reports_untrusted_certificates(self):
        with patch.object(dev_env.socket, "create_connection", side_effect=ssl.SSLCertVerificationError()):
            with self.assertRaisesRegex(ValueError, "network administrator"):
                dev_env.check_boat_tls("https://boat.dev/api/v1")

    def test_tls_preflight_allows_http_development_endpoint_and_rejects_invalid_urls(self):
        with patch.object(dev_env.socket, "create_connection") as connect:
            dev_env.check_boat_tls("http://localhost:8080/api/v1")
            connect.assert_not_called()
        for url in ("not-a-url", "file:///tmp/boat", "https://"):
            with self.subTest(url=url):
                with self.assertRaisesRegex(ValueError, "BOAT_BASE_URL"):
                    dev_env.check_boat_tls(url)

    def test_backend_dotenv_ca_reaches_boat_sdk_with_verification_enabled(self):
        (self.root / ".env").write_text(f"SSL_CERT_FILE='{self.ca_file}'\n")
        environment = {
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "backend"),
            "BOAT_API_KEY": "fixture",
            "TOKEN_ENCRYPTION_KEYS": Fernet.generate_key().decode(),
            "HOOK_TOKEN": "fixture",
            "PERMISSION_HOOK_BASE_URL": "http://localhost:8000",
        }
        code = """
import ssl
import sys
from app.core.config import settings
from app.services.box_operations import ascii_box_service
from boat_sdk import ApiClient
assert settings.SSL_CERT_FILE == sys.argv[1]
configuration = ascii_box_service._configuration()
assert configuration.verify_ssl is True
assert configuration.ssl_ca_cert == sys.argv[1]
with ApiClient(configuration) as client:
    options = client.rest_client.pool_manager.connection_pool_kw
    assert options['cert_reqs'] == ssl.CERT_REQUIRED
    assert options['ca_certs'] == sys.argv[1]
"""
        result = subprocess.run(
            [sys.executable, "-c", code, str(self.ca_file)],
            cwd=self.root, env=environment, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_boat_key_returns_actionable_error_without_network_calls(self):
        (self.root / ".env").write_text(f"SSL_CERT_FILE='{self.ca_file}'\n")
        environment = {
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "backend"),
            "BOAT_API_KEY": "",
            "TOKEN_ENCRYPTION_KEYS": Fernet.generate_key().decode(),
            "HOOK_TOKEN": "fixture",
            "PERMISSION_HOOK_BASE_URL": "http://localhost:8000",
            "LOOPBACK_TRUST": "all",
        }
        code = """
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app.main import app
from app.services.box_operations import BoatNotConfigured
with patch('app.services.box_operations.ApiClient') as remote, \
     patch('app.services.user_sandbox_service.ensure', new=AsyncMock(side_effect=BoatNotConfigured('Boat sandboxes are not configured'))):
    client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000))
    for path in ('/api/v1/sandboxes', '/api/v1/sandboxes/ensure'):
        response = client.post(path, json={'user_id': 'fixture-user'})
        assert response.status_code == 503, response.text
        assert 'Boat sandboxes are not configured' in response.json()['detail']
    remote.assert_not_called()
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=self.root, env=environment, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

"""
Unit tests for Zoho Attachment Bridge.

All tests use mocks, local temporary files, and stdlib unittest.
No external network calls, no live secrets.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, call, patch

# Ensure scripts directory is importable
import sys
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import bridge
import onboarding
import zoho_attach


class TestDCResolution(unittest.TestCase):
    """Test data center mapping and URL generation."""

    def test_all_supported_dcs(self):
        expected = {
            "eu": "zoho.eu",
            "com": "zoho.com",
            "in": "zoho.in",
            "com.au": "zoho.com.au",
            "jp": "zoho.jp",
            "ca": "zohocloud.ca",
            "sa": "zoho.sa",
            "com.cn": "zoho.com.cn",
        }
        for dc, domain in expected.items():
            self.assertEqual(bridge.resolve_dc(dc), domain)
            self.assertEqual(bridge.accounts_base_url(dc), f"https://accounts.{domain}")

    def test_books_base_url_uses_zohoapis_host(self):
        expected = {
            "eu": "https://www.zohoapis.eu/books/v3",
            "com": "https://www.zohoapis.com/books/v3",
            "in": "https://www.zohoapis.in/books/v3",
            "com.au": "https://www.zohoapis.com.au/books/v3",
            "jp": "https://www.zohoapis.jp/books/v3",
            "ca": "https://www.zohoapis.ca/books/v3",
            "sa": "https://www.zohoapis.sa/books/v3",
            "com.cn": "https://www.zohoapis.com.cn/books/v3",
        }
        for dc, url in expected.items():
            self.assertEqual(bridge.books_base_url(dc), url)

    def test_books_base_url_rejects_invalid_dc(self):
        with self.assertRaises(ValueError):
            bridge.books_base_url("invalid_dc")

    def test_case_insensitive_and_whitespace(self):
        self.assertEqual(bridge.resolve_dc("  EU  "), "zoho.eu")
        self.assertEqual(bridge.resolve_dc("COM.AU"), "zoho.com.au")

    def test_invalid_dc_raises_value_error(self):
        with self.assertRaises(ValueError):
            bridge.resolve_dc("invalid_dc")


class TestFileValidationAndMime(unittest.TestCase):
    """Test extension allowlists and MIME type detection."""

    def test_expense_receipt_allowlist(self):
        # Documented Books allowlist: gif, png, jpeg, jpg, bmp, pdf, xls, xlsx, doc, docx
        valid = ["doc.pdf", "img.jpg", "img.jpeg", "img.PNG", "sheet.xlsx", "letter.docx"]
        for fn in valid:
            ext = bridge.validate_file_extension(fn, "expense-receipt")
            self.assertTrue(ext.startswith("."))

        invalid = ["data.csv", "notes.txt", "scan.tiff", "archive.zip", "script.sh"]
        for fn in invalid:
            with self.assertRaises(ValueError):
                bridge.validate_file_extension(fn, "expense-receipt")

    def test_bill_attachment_allowlist(self):
        # Documented Books allowlist: gif, png, jpeg, jpg, bmp, pdf
        valid = ["doc.pdf", "img.jpg", "img.GIF", "img.bmp", "img.png"]
        for fn in valid:
            ext = bridge.validate_file_extension(fn, "bill-attachment")
            self.assertTrue(ext.startswith("."))

        invalid = ["table.xlsx", "file.doc", "data.csv", "notes.txt", "archive.zip"]
        for fn in invalid:
            with self.assertRaises(ValueError):
                bridge.validate_file_extension(fn, "bill-attachment")

    def test_unknown_target_raises(self):
        with self.assertRaises(ValueError):
            bridge.validate_file_extension("file.pdf", "unknown-target")

    def test_mime_type_guessing(self):
        self.assertEqual(bridge.guess_mime_type("file.pdf"), "application/pdf")
        self.assertEqual(bridge.guess_mime_type("photo.JPEG"), "image/jpeg")
        self.assertEqual(bridge.guess_mime_type("image.gif"), "image/gif")
        self.assertEqual(bridge.guess_mime_type("sheet.xlsx"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertEqual(bridge.guess_mime_type("unknown.xyz"), "application/octet-stream")


class TestSHA256(unittest.TestCase):
    """Test SHA-256 calculation."""

    def test_sha256_bytes_and_file(self):
        content = b"Zoho Attachment Bridge Test Binary Content \x00\x01\x02"
        expected = bridge.sha256_bytes(content)

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            file_hash = bridge.sha256_file(tmp_path)
            self.assertEqual(file_hash, expected)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


class TestMultipartBody(unittest.TestCase):
    """Test multipart/form-data generation."""

    def test_multipart_body_structure(self):
        content = b"PDF-1.4 Mock Receipt Bytes"
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            body, content_type = bridge.build_multipart_body(tmp_path, field_name="receipt")
            self.assertIn("multipart/form-data; boundary=", content_type)
            boundary = content_type.split("boundary=")[1]

            # Verify body contains boundary, disposition, content type and raw bytes
            self.assertIn(f"--{boundary}".encode("utf-8"), body)
            self.assertIn(b'Content-Disposition: form-data; name="receipt"; filename="', body)
            self.assertIn(b"Content-Type: application/pdf", body)
            self.assertIn(content, body)
            self.assertTrue(body.endswith(f"--{boundary}--\r\n".encode("utf-8")))
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


class TestEnvManagement(unittest.TestCase):
    """Test env file loading, safe updating, and permission setting."""

    def test_parse_env_content(self):
        sample = """
        # Comment line
        VAR_ONE=hello
        VAR_TWO="world with spaces"
        VAR_THREE='single quoted'
        # Another comment
        EMPTY_VAR=
        """
        parsed = bridge.parse_env_content(sample)
        self.assertEqual(parsed["VAR_ONE"], "hello")
        self.assertEqual(parsed["VAR_TWO"], "world with spaces")
        self.assertEqual(parsed["VAR_THREE"], "single quoted")
        self.assertNotIn("# Comment line", parsed)

    def test_update_env_file_preserves_comments_and_sets_mode_0600(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            initial_content = "# Configuration\nEXISTING_KEY=old_value\n\n# Keep this comment\nOTHER_KEY=keep_me\n"
            env_file.write_text(initial_content, encoding="utf-8")

            updates = {
                "EXISTING_KEY": "new_value",
                "ZOHO_BRIDGE_CLIENT_ID": "1000.TESTCLIENTID",
                "ZOHO_BRIDGE_DC": "eu",
            }
            bridge.update_env_file(env_file, updates)

            result = env_file.read_text(encoding="utf-8")
            self.assertIn("# Configuration", result)
            self.assertIn("# Keep this comment", result)
            self.assertIn("OTHER_KEY=keep_me", result)
            self.assertIn("EXISTING_KEY=new_value", result)
            self.assertIn("ZOHO_BRIDGE_CLIENT_ID=1000.TESTCLIENTID", result)
            self.assertIn("ZOHO_BRIDGE_DC=eu", result)

            # Check permissions
            mode = stat.S_IMODE(env_file.stat().st_mode)
            self.assertEqual(mode, 0o600)

    def test_load_env_precedence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            local_env = Path(tmpdir) / ".env"
            local_env.write_text(
                "ZOHO_BRIDGE_CLIENT_ID=file_client_id\n"
                "ZOHO_BRIDGE_CLIENT_SECRET=file_secret\n"
                "ZOHO_BRIDGE_REFRESH_TOKEN=file_refresh\n"
                "ZOHO_BRIDGE_DC=com\n",
                encoding="utf-8",
            )

            # Patch cwd to tmpdir, home to tmpdir (avoid reading ~/.openclaw/.env), and clear env vars
            with patch("pathlib.Path.cwd", return_value=Path(tmpdir)), \
                 patch("pathlib.Path.home", return_value=Path(tmpdir)):
                with patch.dict(os.environ, {}, clear=True):
                    # Should load from local .env
                    loaded = bridge.load_env()
                    self.assertEqual(loaded["client_id"], "file_client_id")
                    self.assertEqual(loaded["dc"], "com")

                    # Test process env precedence over file
                    with patch.dict(os.environ, {"ZOHO_BRIDGE_CLIENT_ID": "env_override_id"}):
                        loaded = bridge.load_env()
                        self.assertEqual(loaded["client_id"], "env_override_id")
                        self.assertEqual(loaded["client_secret"], "file_secret")

    def test_load_env_named_profile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            local_env = Path(tmpdir) / ".env"
            local_env.write_text(
                "ZOHO_BRIDGE_ACME_CLIENT_ID=acme_id\n"
                "ZOHO_BRIDGE_ACME_CLIENT_SECRET=acme_sec\n"
                "ZOHO_BRIDGE_ACME_REFRESH_TOKEN=acme_tok\n"
                "ZOHO_BRIDGE_ACME_DC=eu\n"
                "ZOHO_BRIDGE_ACME_BOOKS_ORG_ID=987654\n",
                encoding="utf-8",
            )
            with patch("pathlib.Path.cwd", return_value=Path(tmpdir)), \
                 patch("pathlib.Path.home", return_value=Path(tmpdir)):
                with patch.dict(os.environ, {}, clear=True):
                    loaded = bridge.load_env(profile="acme")
                    self.assertEqual(loaded["client_id"], "acme_id")
                    self.assertEqual(loaded["books_org_id"], "987654")

    def test_load_env_missing_vars_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("pathlib.Path.cwd", return_value=Path(tmpdir)), \
                 patch("pathlib.Path.home", return_value=Path(tmpdir)):
                with patch.dict(os.environ, {}, clear=True):
                    with self.assertRaises(EnvironmentError):
                        bridge.load_env()


class TestOAuthAndHttp(unittest.TestCase):
    """Test OAuth exchange, refresh, and HTTP backoff."""

    @patch("urllib.request.urlopen")
    def test_exchange_grant_token_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "access_token": "1000.mock_access",
            "refresh_token": "1000.mock_refresh",
            "expires_in": 3600,
            "token_type": "Bearer",
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        data = bridge.exchange_grant_token("cid", "csec", "code123", "eu")
        self.assertEqual(data["refresh_token"], "1000.mock_refresh")
        self.assertEqual(data["access_token"], "1000.mock_access")

    @patch("urllib.request.urlopen")
    def test_refresh_access_token_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "access_token": "1000.fresh_access_token",
            "expires_in": 3600,
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        token = bridge.refresh_access_token("cid", "csec", "ref_tok", "com")
        self.assertEqual(token, "1000.fresh_access_token")

    @patch("urllib.request.urlopen")
    def test_refresh_access_token_oauth_error(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "error": "invalid_code",
            "error_description": "Grant code has expired",
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        with self.assertRaises(RuntimeError) as ctx:
            bridge.refresh_access_token("cid", "csec", "ref_tok", "eu")
        self.assertIn("invalid_code", str(ctx.exception))
        self.assertIn("Grant code has expired", str(ctx.exception))

    @patch("time.sleep")
    @patch("bridge._execute_http")
    def test_api_request_429_retry_with_header(self, mock_exec, mock_sleep):
        # First call returns 429 with retry-after header
        # Second call returns 200
        mock_exec.side_effect = [
            (429, b'{"message": "Rate limit exceeded"}', {"retry-after": "2"}),
            (200, b'{"code": 0, "message": "success"}', {}),
        ]

        status, body = bridge.api_request("https://api.zoho.eu/test", "mock_token")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["code"], 0)
        mock_sleep.assert_called_once_with(2)


class TestBooksOperationsAndVerification(unittest.TestCase):
    """Test Books upload and read-back verification flows."""

    @patch("bridge.api_request")
    def test_upload_books_expense_receipt(self, mock_api):
        mock_api.return_value = (201, json.dumps({
            "code": 0,
            "message": "The receipt has been attached.",
        }).encode("utf-8"))

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"PDF receipt data")
            tmp_path = tmp.name

        try:
            res = bridge.upload_books_expense_receipt(
                dc="eu",
                access_token="tok",
                organization_id="12345",
                expense_id="exp999",
                file_path=tmp_path,
            )
            self.assertEqual(res["code"], 0)
            mock_api.assert_called_once()
            # Verify URL
            args, kwargs = mock_api.call_args
            self.assertIn("/expenses/exp999/receipt?organization_id=12345", args[0])
            self.assertEqual(kwargs.get("method"), "POST")
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @patch("bridge.api_request")
    def test_upload_books_bill_attachment(self, mock_api):
        mock_api.return_value = (200, json.dumps({
            "code": 0,
            "message": "Document attached.",
        }).encode("utf-8"))

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"PDF bill data")
            tmp_path = tmp.name

        try:
            res = bridge.upload_books_bill_attachment(
                dc="eu",
                access_token="tok",
                organization_id="12345",
                bill_id="bill888",
                file_path=tmp_path,
            )
            self.assertEqual(res["code"], 0)
            args, kwargs = mock_api.call_args
            self.assertIn("/bills/bill888/attachment?organization_id=12345", args[0])
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @patch("bridge.download_books_expense_receipt")
    def test_verify_books_expense_receipt_match(self, mock_download):
        raw_bytes = b"Exact same receipt content"
        expected_sha = bridge.sha256_bytes(raw_bytes)
        mock_download.return_value = raw_bytes

        verified, msg = bridge.verify_books_expense_receipt(
            dc="eu",
            access_token="tok",
            organization_id="123",
            expense_id="exp1",
            expected_sha256=expected_sha,
        )
        self.assertTrue(verified)
        self.assertIn("Verified", msg)

    @patch("bridge.download_books_expense_receipt")
    def test_verify_books_expense_receipt_mismatch(self, mock_download):
        raw_bytes = b"Corrupted receipt content"
        expected_sha = bridge.sha256_bytes(b"Original content")
        mock_download.return_value = raw_bytes

        verified, msg = bridge.verify_books_expense_receipt(
            dc="eu",
            access_token="tok",
            organization_id="123",
            expense_id="exp1",
            expected_sha256=expected_sha,
        )
        self.assertFalse(verified)
        self.assertIn("mismatch", msg)

    @patch("bridge.download_books_bill_attachment")
    def test_verify_books_bill_attachment_match(self, mock_download):
        raw_bytes = b"Bill document binary"
        expected_sha = bridge.sha256_bytes(raw_bytes)
        mock_download.return_value = raw_bytes

        verified, msg = bridge.verify_books_bill_attachment(
            dc="com",
            access_token="tok",
            organization_id="123",
            bill_id="b1",
            expected_sha256=expected_sha,
        )
        self.assertTrue(verified)
        self.assertIn("Verified", msg)


class TestCliZohoAttach(unittest.TestCase):
    """Test zoho_attach CLI execution."""

    def test_file_not_found_returns_error(self):
        ret = zoho_attach.main([
            "--app", "books",
            "--target", "expense-receipt",
            "--id", "123",
            "--file", "/nonexistent/file.pdf",
            "--organization-id", "999",
        ])
        self.assertEqual(ret, 1)

    def test_disallowed_extension_returns_error(self):
        with tempfile.NamedTemporaryFile(suffix=".sh", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            ret = zoho_attach.main([
                "--app", "books",
                "--target", "expense-receipt",
                "--id", "123",
                "--file", tmp_path,
                "--organization-id", "999",
            ])
            self.assertEqual(ret, 1)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @patch("zoho_attach.verify_books_expense_receipt", return_value=(True, "Verified match"))
    @patch("zoho_attach.upload_books_expense_receipt", return_value={"code": 0, "message": "Uploaded"})
    @patch("zoho_attach.refresh_access_token", return_value="mock_access_token")
    @patch("zoho_attach.load_env")
    def test_successful_upload_and_verification(self, mock_env, mock_tok, mock_up, mock_ver):
        mock_env.return_value = {
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "reftok",
            "dc": "eu",
            "books_org_id": "org123",
        }

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"Receipt bytes")
            tmp_path = tmp.name

        try:
            ret = zoho_attach.main([
                "--app", "books",
                "--target", "expense-receipt",
                "--id", "exp_100",
                "--file", tmp_path,
            ])
            self.assertEqual(ret, 0)
            mock_tok.assert_called_once()
            mock_up.assert_called_once()
            mock_ver.assert_called_once()
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


class TestCliOnboarding(unittest.TestCase):
    """Test onboarding script."""

    @patch("onboarding.update_env_file")
    @patch("onboarding.exchange_grant_token")
    @patch("onboarding.prompt_input")
    def test_onboarding_flow(self, mock_input, mock_exchange, mock_update):
        mock_input.side_effect = [
            "eu",               # DC
            "1000.CLIENTID",    # client_id
            "my_client_sec",    # client_secret
            "1000.GRANTCODE",   # grant_code
            "",                 # profile (default)
        ]
        mock_exchange.return_value = {
            "refresh_token": "1000.REFRESH_TOKEN",
            "access_token": "1000.ACCESS_TOKEN",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            target_env = str(Path(tmpdir) / ".env")
            ret = onboarding.main(["--env-file", target_env])
            self.assertEqual(ret, 0)
            mock_exchange.assert_called_once_with(
                client_id="1000.CLIENTID",
                client_secret="my_client_sec",
                code="1000.GRANTCODE",
                dc="eu",
            )
            mock_update.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class TestTokenCache(unittest.TestCase):
    """Access token caching prevents Zoho token-endpoint rate limiting."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cache = os.path.join(self.tmpdir, "tokens.json")
        os.environ["ZOHO_BRIDGE_TOKEN_CACHE"] = self.cache

    def tearDown(self):
        os.environ.pop("ZOHO_BRIDGE_TOKEN_CACHE", None)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _fake_response(self, payload):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = lambda s, *a: False
        return mock_resp

    def test_second_call_uses_cache_and_skips_network(self):
        payload = {"access_token": "tok_cached", "expires_in": 3600}
        with patch("urllib.request.urlopen", return_value=self._fake_response(payload)) as mock_open:
            first = bridge.refresh_access_token("cid", "csec", "rtok", "eu")
            self.assertEqual(first, "tok_cached")
            self.assertEqual(mock_open.call_count, 1)

        # Second call must be served from cache, without any HTTP request.
        with patch("urllib.request.urlopen", side_effect=AssertionError("network used")) as mock_open2:
            second = bridge.refresh_access_token("cid", "csec", "rtok", "eu")
            self.assertEqual(second, "tok_cached")
            self.assertEqual(mock_open2.call_count, 0)

    def test_cache_file_has_mode_0600_and_no_plaintext_secrets(self):
        payload = {"access_token": "tok_secret", "expires_in": 3600}
        with patch("urllib.request.urlopen", return_value=self._fake_response(payload)):
            bridge.refresh_access_token("cid", "csec", "rtok", "eu")

        mode = stat.S_IMODE(os.stat(self.cache).st_mode)
        self.assertEqual(mode, 0o600)

        raw = open(self.cache, encoding="utf-8").read()
        self.assertNotIn("csec", raw)
        self.assertNotIn("rtok", raw)
        self.assertNotIn("cid", raw)

    def test_expired_entry_triggers_refresh(self):
        payload = {"access_token": "tok_a", "expires_in": 3600}
        with patch("urllib.request.urlopen", return_value=self._fake_response(payload)):
            bridge.refresh_access_token("cid", "csec", "rtok", "eu")

        entries = json.loads(open(self.cache, encoding="utf-8").read())
        for key in entries:
            entries[key]["expires_at"] = time.time() - 10
        with open(self.cache, "w", encoding="utf-8") as fh:
            json.dump(entries, fh)

        payload2 = {"access_token": "tok_b", "expires_in": 3600}
        with patch("urllib.request.urlopen", return_value=self._fake_response(payload2)) as mock_open:
            token = bridge.refresh_access_token("cid", "csec", "rtok", "eu")
            self.assertEqual(token, "tok_b")
            self.assertEqual(mock_open.call_count, 1)

    def test_use_cache_false_always_refreshes(self):
        payload = {"access_token": "tok_x", "expires_in": 3600}
        with patch("urllib.request.urlopen", return_value=self._fake_response(payload)) as mock_open:
            bridge.refresh_access_token("cid", "csec", "rtok", "eu")
            bridge.refresh_access_token("cid", "csec", "rtok", "eu", use_cache=False)
            self.assertEqual(mock_open.call_count, 2)

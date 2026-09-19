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

    def test_crm_base_url_uses_zohoapis_host(self):
        expected = {
            "eu": "https://www.zohoapis.eu/crm/v8",
            "com": "https://www.zohoapis.com/crm/v8",
            "ca": "https://www.zohoapis.ca/crm/v8",
        }
        for dc, url in expected.items():
            self.assertEqual(bridge.crm_base_url(dc), url)

    def test_crm_base_url_rejects_invalid_dc(self):
        with self.assertRaises(ValueError):
            bridge.crm_base_url("invalid_dc")

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

    def test_crm_record_attachment_allowlist(self):
        # Zoho does not document an extension allowlist for the CRM v8 record
        # attachment endpoint. The bridge must not invent one.
        valid = ["document.pdf", "image.webp", "data.csv", "archive.zip", "installer.exe"]
        for fn in valid:
            ext = bridge.validate_file_extension(fn, "record-attachment")
            self.assertTrue(ext.startswith("."))

        with self.assertRaises(ValueError):
            bridge.validate_file_extension("extensionless", "record-attachment")

    def test_journal_attachment_has_no_invented_allowlist(self):
        # Journals API publishes no extension allowlist. Require an extension
        # and leave type enforcement to Zoho.
        valid = ["receipt.pdf", "scan.webp", "notes.csv", "archive.zip"]
        for fn in valid:
            ext = bridge.validate_file_extension(fn, "journal-attachment")
            self.assertTrue(ext.startswith("."))

        with self.assertRaises(ValueError):
            bridge.validate_file_extension("extensionless", "journal-attachment")

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


class TestFileSizeValidation(unittest.TestCase):
    """Target-specific upload limits are checked before multipart construction."""

    def test_documented_default_limits(self):
        self.assertEqual(
            bridge.get_max_upload_bytes("expense-receipt"), 7 * 1024 * 1024
        )
        self.assertEqual(
            bridge.get_max_upload_bytes("bill-attachment"), 5 * 1024 * 1024
        )
        self.assertIsNone(bridge.get_max_upload_bytes("record-attachment"))
        self.assertIsNone(bridge.get_max_upload_bytes("journal-attachment"))
        self.assertEqual(
            bridge.get_max_upload_bytes("file-upload"), 250 * 1024 * 1024
        )

    def test_target_env_override(self):
        with patch.dict(
            os.environ,
            {"ZOHO_BRIDGE_MAX_BYTES_BILL_ATTACHMENT": "1234"},
            clear=True,
        ):
            self.assertEqual(bridge.get_max_upload_bytes("bill-attachment"), 1234)

    def test_profile_env_override_precedes_default_env(self):
        with patch.dict(
            os.environ,
            {
                "ZOHO_BRIDGE_MAX_BYTES_EXPENSE_RECEIPT": "2000",
                "ZOHO_BRIDGE_ACME_MAX_BYTES_EXPENSE_RECEIPT": "1500",
            },
            clear=True,
        ):
            self.assertEqual(
                bridge.get_max_upload_bytes("expense-receipt", profile="acme"),
                1500,
            )

    def test_explicit_override_precedes_environment(self):
        with patch.dict(
            os.environ,
            {"ZOHO_BRIDGE_MAX_BYTES_BILL_ATTACHMENT": "1234"},
            clear=True,
        ):
            self.assertEqual(
                bridge.get_max_upload_bytes("bill-attachment", override_bytes=500),
                500,
            )

    def test_invalid_env_limit_is_rejected(self):
        with patch.dict(
            os.environ,
            {"ZOHO_BRIDGE_MAX_BYTES_BILL_ATTACHMENT": "not-a-number"},
            clear=True,
        ):
            with self.assertRaises(ValueError) as ctx:
                bridge.get_max_upload_bytes("bill-attachment")
        self.assertIn("ZOHO_BRIDGE_MAX_BYTES_BILL_ATTACHMENT", str(ctx.exception))

    def test_oversized_file_is_rejected_with_limit_name(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"123456")
            tmp_path = tmp.name
        try:
            with self.assertRaises(ValueError) as ctx:
                bridge.validate_file_size(
                    tmp_path, "bill-attachment", override_bytes=5
                )
            self.assertIn("bill-attachment", str(ctx.exception))
            self.assertIn("configured limit", str(ctx.exception))
        finally:
            os.unlink(tmp_path)

    @patch("bridge.api_request")
    def test_cli_override_reaches_upload_function(self, mock_api):
        mock_api.return_value = (200, b'{"code":0}')
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"larger-than-five-bytes")
            tmp_path = tmp.name
        try:
            with patch.dict(os.environ, {"ZOHO_BRIDGE_MAX_BYTES_BILL_ATTACHMENT": "5"}, clear=False):
                with self.assertRaises(ValueError) as ctx:
                    bridge.upload_books_bill_attachment(
                        dc="eu",
                        access_token="tok",
                        organization_id="123",
                        bill_id="456",
                        file_path=tmp_path,
                    )
            self.assertIn("configured limit", str(ctx.exception))
        finally:
            os.unlink(tmp_path)

    @patch("bridge.build_multipart_body")
    def test_books_rejection_happens_before_multipart_construction(self, mock_build):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"tiny")
            tmp_path = tmp.name
        try:
            with patch("bridge.os.path.getsize", return_value=bridge.DEFAULT_MAX_UPLOAD_BYTES["bill-attachment"] + 1):
                with self.assertRaises(ValueError) as ctx:
                    bridge.upload_books_bill_attachment(
                        dc="eu",
                        access_token="tok",
                        organization_id="123",
                        bill_id="456",
                        file_path=tmp_path,
                    )
            self.assertIn("5 MB", str(ctx.exception))
            mock_build.assert_not_called()
        finally:
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

    @patch("bridge.api_request")
    def test_upload_books_journal_attachment(self, mock_api):
        mock_api.return_value = (201, json.dumps({
            "code": 0,
            "message": "Your file has been successfully attached to the journal.",
        }).encode("utf-8"))

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"PDF journal attachment data")
            tmp_path = tmp.name

        try:
            res = bridge.upload_books_journal_attachment(
                dc="eu",
                access_token="tok",
                organization_id="12345",
                journal_id="460000000038001",
                file_path=tmp_path,
            )
            self.assertEqual(res["code"], 0)
            args, kwargs = mock_api.call_args
            self.assertIn(
                "/journals/460000000038001/attachment?organization_id=12345",
                args[0],
            )
            self.assertEqual(kwargs.get("method"), "POST")
            self.assertIn(b'name="attachment"', kwargs["data"])
            self.assertIn(b"PDF journal attachment data", kwargs["data"])
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @patch("bridge.download_books_journal_document")
    @patch("bridge.get_books_journal")
    def test_verify_books_journal_attachment_match(self, mock_get, mock_download):
        raw_bytes = b"Journal document payload"
        expected_sha = bridge.sha256_bytes(raw_bytes)
        mock_get.return_value = {
            "journal": {
                "journal_id": "460000000038001",
                "documents": [
                    {
                        "document_id": "460000000123001",
                        "file_name": "receipt.pdf",
                    }
                ],
            }
        }
        mock_download.return_value = raw_bytes

        verified, msg = bridge.verify_books_journal_attachment(
            dc="eu",
            access_token="tok",
            organization_id="12345",
            journal_id="460000000038001",
            file_name="receipt.pdf",
            expected_sha256=expected_sha,
        )
        self.assertTrue(verified)
        self.assertIn("Verified", msg)
        mock_download.assert_called_once_with(
            "eu", "tok", "12345", "460000000038001", "460000000123001"
        )

    @patch("bridge.download_books_journal_document")
    @patch("bridge.get_books_journal")
    def test_verify_books_journal_attachment_mismatch(self, mock_get, mock_download):
        raw_bytes = b"Corrupted journal bytes"
        expected_sha = bridge.sha256_bytes(b"Original file bytes")
        mock_get.return_value = {
            "journal": {
                "journal_id": "460000000038001",
                "documents": [
                    {
                        "document_id": "460000000123001",
                        "file_name": "receipt.pdf",
                    }
                ],
            }
        }
        mock_download.return_value = raw_bytes

        verified, msg = bridge.verify_books_journal_attachment(
            dc="eu",
            access_token="tok",
            organization_id="12345",
            journal_id="460000000038001",
            file_name="receipt.pdf",
            expected_sha256=expected_sha,
        )
        self.assertFalse(verified)
        self.assertIn("mismatch", msg)

    @patch("bridge.get_books_journal")
    def test_verify_books_journal_attachment_not_found(self, mock_get):
        mock_get.return_value = {
            "journal": {
                "journal_id": "460000000038001",
                "documents": [],
            }
        }
        verified, msg = bridge.verify_books_journal_attachment(
            dc="eu",
            access_token="tok",
            organization_id="12345",
            journal_id="460000000038001",
            file_name="missing.pdf",
            expected_sha256="a" * 64,
        )
        self.assertFalse(verified)
        self.assertIn("not found", msg)

    @patch("bridge.get_books_journal")
    def test_verify_books_journal_attachment_rejects_ambiguous_filename(self, mock_get):
        mock_get.return_value = {
            "journal": {
                "journal_id": "460000000038001",
                "documents": [
                    {"document_id": "1", "file_name": "dup.pdf"},
                    {"document_id": "2", "file_name": "dup.pdf"},
                ],
            }
        }
        verified, msg = bridge.verify_books_journal_attachment(
            dc="eu",
            access_token="tok",
            organization_id="12345",
            journal_id="460000000038001",
            file_name="dup.pdf",
            expected_sha256="a" * 64,
        )
        self.assertFalse(verified)
        self.assertIn("multiple journal documents", msg)

    @patch("bridge.api_request")
    def test_download_books_journal_document_uses_document_path(self, mock_api):
        mock_api.return_value = (200, b"%PDF-1.4 journal bytes")
        data = bridge.download_books_journal_document(
            "eu", "tok", "12345", "460000000038001", "460000000123001"
        )
        self.assertEqual(data, b"%PDF-1.4 journal bytes")
        mock_api.assert_called_once_with(
            "https://www.zohoapis.eu/books/v3/journals/460000000038001/documents/460000000123001?organization_id=12345",
            "tok",
            method="GET",
        )

    def test_journal_ids_reject_injection(self):
        with self.assertRaises(ValueError):
            bridge.validate_zoho_id("123?fields=all", "journal ID")
        with self.assertRaises(ValueError):
            bridge.upload_books_journal_attachment(
                dc="eu",
                access_token="tok",
                organization_id="12345",
                journal_id="460000000038001/../attachment",
                file_path="receipt.pdf",
            )


class TestCrmOperationsAndVerification(unittest.TestCase):
    """Test CRM v8 record attachment upload and mandatory read-back flow."""

    @patch("bridge.api_request")
    def test_upload_crm_record_attachment_uses_v8_file_multipart_endpoint(self, mock_api):
        mock_api.return_value = (201, json.dumps({
            "data": [{
                "code": "SUCCESS",
                "details": {"id": "4876876000001021001"},
                "message": "attachment added successfully",
                "status": "success",
            }],
        }).encode("utf-8"))

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"CRM attachment data")
            tmp_path = tmp.name

        try:
            response = bridge.upload_crm_record_attachment(
                dc="eu",
                access_token="tok",
                module="Leads",
                record_id="4876876000000376008",
                file_path=tmp_path,
            )
            self.assertEqual(response["data"][0]["details"]["id"], "4876876000001021001")
            mock_api.assert_called_once()
            args, kwargs = mock_api.call_args
            self.assertEqual(
                args[0],
                "https://www.zohoapis.eu/crm/v8/Leads/4876876000000376008/Attachments",
            )
            self.assertEqual(kwargs["method"], "POST")
            self.assertIn("multipart/form-data; boundary=", kwargs["content_type"])
            self.assertIn(b'name="file"', kwargs["data"])
            self.assertIn(b"CRM attachment data", kwargs["data"])
        finally:
            os.unlink(tmp_path)

    @patch("bridge.api_request")
    def test_list_crm_record_attachments_requests_id_and_filename_fields(self, mock_api):
        mock_api.return_value = (200, json.dumps({
            "data": [{"id": "4876876000001021001", "File_Name": "invoice.pdf"}],
        }).encode("utf-8"))

        attachments = bridge.list_crm_record_attachments(
            "com", "tok", "Deals", "4876876000000376008"
        )

        self.assertEqual(attachments, [{"id": "4876876000001021001", "File_Name": "invoice.pdf"}])
        mock_api.assert_called_once_with(
            "https://www.zohoapis.com/crm/v8/Deals/4876876000000376008/Attachments?fields=id%2CFile_Name",
            "tok",
            method="GET",
        )

    @patch("bridge.api_request")
    def test_download_crm_record_attachment_uses_attachment_endpoint(self, mock_api):
        mock_api.return_value = (200, b"downloaded CRM attachment")

        data = bridge.download_crm_record_attachment(
            "ca", "tok", "Contacts", "4876876000000376008", "4876876000001021001"
        )

        self.assertEqual(data, b"downloaded CRM attachment")
        mock_api.assert_called_once_with(
            "https://www.zohoapis.ca/crm/v8/Contacts/4876876000000376008/Attachments/4876876000001021001",
            "tok",
            method="GET",
        )

    @patch("bridge.download_crm_record_attachment")
    @patch("bridge.list_crm_record_attachments")
    def test_verify_crm_record_attachment_lists_finds_downloads_and_hashes(
        self, mock_list, mock_download
    ):
        raw_bytes = b"exact CRM attachment bytes"
        mock_list.return_value = [
            {"id": "4876876000001021000", "File_Name": "old.pdf"},
            {"id": "4876876000001021001", "File_Name": "invoice.pdf"},
        ]
        mock_download.return_value = raw_bytes

        verified, message = bridge.verify_crm_record_attachment(
            dc="eu",
            access_token="tok",
            module="Deals",
            record_id="4876876000000376008",
            file_name="invoice.pdf",
            expected_sha256=bridge.sha256_bytes(raw_bytes),
            attachment_id="4876876000001021001",
        )

        self.assertTrue(verified)
        self.assertIn("Verified", message)
        mock_list.assert_called_once_with("eu", "tok", "Deals", "4876876000000376008")
        mock_download.assert_called_once_with("eu", "tok", "Deals", "4876876000000376008", "4876876000001021001")

    @patch("bridge.download_crm_record_attachment")
    @patch("bridge.list_crm_record_attachments")
    def test_verify_crm_record_attachment_uses_filename_when_upload_has_no_id(
        self, mock_list, mock_download
    ):
        raw_bytes = b"exact CRM attachment bytes"
        mock_list.return_value = [
            {"id": "4876876000001021001", "File_Name": "invoice.pdf"}
        ]
        mock_download.return_value = raw_bytes

        verified, _ = bridge.verify_crm_record_attachment(
            "eu", "tok", "Deals", "4876876000000376008", "invoice.pdf",
            bridge.sha256_bytes(raw_bytes),
        )

        self.assertTrue(verified)
        mock_download.assert_called_once_with("eu", "tok", "Deals", "4876876000000376008", "4876876000001021001")

    @patch("bridge.list_crm_record_attachments", return_value=[])
    def test_verify_crm_record_attachment_fails_when_not_listed(self, mock_list):
        verified, message = bridge.verify_crm_record_attachment(
            "eu", "tok", "Deals", "4876876000000376008", "missing.pdf", "a" * 64
        )

        self.assertFalse(verified)
        self.assertIn("not found", message)

    @patch("bridge.download_crm_record_attachment")
    @patch("bridge.list_crm_record_attachments")
    def test_verify_crm_record_attachment_rejects_ambiguous_filename(
        self, mock_list, mock_download
    ):
        mock_list.return_value = [
            {"id": "4876876000001021001", "File_Name": "invoice.pdf"},
            {"id": "4876876000001021002", "File_Name": "invoice.pdf"},
        ]

        verified, message = bridge.verify_crm_record_attachment(
            "eu", "tok", "Deals", "4876876000000376008", "invoice.pdf", "a" * 64
        )

        self.assertFalse(verified)
        self.assertIn("multiple CRM attachments", message)
        mock_download.assert_not_called()

    def test_crm_url_components_reject_injection(self):
        with self.assertRaises(ValueError):
            bridge.validate_crm_module("Deals/123/Attachments")
        with self.assertRaises(ValueError):
            bridge.validate_zoho_id("123?fields=all", "CRM record ID")

    def test_parse_error_does_not_accept_empty_http_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            bridge.parse_zoho_response(b"", 403, "CRM attachment upload")
        self.assertIn("HTTP 403", str(ctx.exception))

    @patch("bridge.download_crm_record_attachment")
    @patch("bridge.list_crm_record_attachments")
    def test_verify_crm_record_attachment_rejects_upload_id_not_in_list(
        self, mock_list, mock_download
    ):
        mock_list.return_value = [{"id": "4876876000001021099", "File_Name": "invoice.pdf"}]

        verified, message = bridge.verify_crm_record_attachment(
            "eu", "tok", "Deals", "4876876000000376008", "invoice.pdf", "a" * 64,
            attachment_id="4876876000001021001",
        )

        self.assertFalse(verified)
        self.assertIn("newly uploaded attachment", message)
        mock_download.assert_not_called()


class TestWorkDriveOperationsAndVerification(unittest.TestCase):
    """WorkDrive upload, new version, and mandatory download read-back."""

    def _temp_file(self, content: bytes = b"WorkDrive binary payload", suffix: str = ".pdf") -> str:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            self.addCleanup(lambda p=tmp.name: os.path.exists(p) and os.unlink(p))
            return tmp.name

    def test_workdrive_base_url_uses_zohoapis_host(self):
        expected = {
            "eu": "https://www.zohoapis.eu/workdrive",
            "com": "https://www.zohoapis.com/workdrive",
            "ca": "https://www.zohoapis.ca/workdrive",
        }
        for dc, url in expected.items():
            self.assertEqual(bridge.workdrive_base_url(dc), url)
        with self.assertRaises(ValueError):
            bridge.workdrive_base_url("invalid_dc")

    def test_workdrive_download_uses_dedicated_download_host(self):
        # Downloads are not served from zohoapis; Canada and Saudi Arabia differ
        # from the simple download.zoho.<tld> pattern.
        expected = {
            "eu": "https://download.zoho.eu",
            "com": "https://download.zoho.com",
            "ca": "https://download.zohocloud.ca",
            "sa": "https://files.zoho.sa",
        }
        for dc, url in expected.items():
            self.assertEqual(bridge.workdrive_download_base_url(dc), url)

    def test_workdrive_resource_id_validation_rejects_injection(self):
        self.assertEqual(
            bridge.validate_workdrive_resource_id("ly9zm0170fb40015f4e2", "folder"),
            "ly9zm0170fb40015f4e2",
        )
        for bad in ["abc/../def", "abc?version=2", "", "abc def"]:
            with self.assertRaises(ValueError):
                bridge.validate_workdrive_resource_id(bad, "folder")

    def test_workdrive_accepts_any_extension_but_requires_one(self):
        for name in ["doc.pdf", "archive.zip", "data.csv", "image.webp"]:
            for target in ("file-upload", "new-version"):
                self.assertTrue(bridge.validate_file_extension(name, target).startswith("."))
        with self.assertRaises(ValueError):
            bridge.validate_file_extension("extensionless", "file-upload")

    @patch("bridge.api_request")
    def test_upload_workdrive_file_posts_multipart_content_and_parent_id(self, mock_api):
        mock_api.return_value = (200, json.dumps({
            "data": [{
                "attributes": {
                    "resource_id": "g4xh1aaaabbbbccccdddd100c5",
                    "parent_id": "ly9zm0170fb40015f4e2",
                    "FileName": "report.pdf",
                },
                "type": "files",
            }],
        }).encode("utf-8"))

        tmp_path = self._temp_file(b"WorkDrive upload bytes")
        response = bridge.upload_workdrive_file(
            dc="eu",
            access_token="tok",
            parent_id="ly9zm0170fb40015f4e2",
            file_path=tmp_path,
        )

        self.assertEqual(
            response["data"][0]["attributes"]["resource_id"],
            "g4xh1aaaabbbbccccdddd100c5",
        )
        args, kwargs = mock_api.call_args
        self.assertEqual(args[0], "https://www.zohoapis.eu/workdrive/api/v1/upload")
        self.assertEqual(kwargs["method"], "POST")
        self.assertIn("multipart/form-data; boundary=", kwargs["content_type"])
        # Zoho names the binary part 'content', not 'file'.
        self.assertIn(b'name="content"', kwargs["data"])
        self.assertIn(b'name="parent_id"', kwargs["data"])
        self.assertIn(b"ly9zm0170fb40015f4e2", kwargs["data"])
        self.assertIn(b"WorkDrive upload bytes", kwargs["data"])
        self.assertIn(b'name="override-name-exist"', kwargs["data"])

    @patch("bridge.api_request")
    def test_new_version_sets_override_name_exist_true(self, mock_api):
        mock_api.return_value = (200, json.dumps({
            "data": [{"attributes": {"resource_id": "res1"}, "type": "files"}],
        }).encode("utf-8"))

        tmp_path = self._temp_file()
        bridge.upload_workdrive_file(
            dc="com",
            access_token="tok",
            parent_id="parentfolder123",
            file_path=tmp_path,
            filename="existing-report.pdf",
            override_name_exist=True,
        )

        body = mock_api.call_args.kwargs["data"]
        self.assertIn(b'name="override-name-exist"', body)
        self.assertIn(b"true", body)
        self.assertIn(b"existing-report.pdf", body)
        # A new version uses the very same upload endpoint.
        self.assertEqual(
            mock_api.call_args.args[0],
            "https://www.zohoapis.com/workdrive/api/v1/upload",
        )

    @patch("bridge.api_request")
    def test_first_upload_sends_override_name_exist_false(self, mock_api):
        mock_api.return_value = (200, json.dumps({"data": []}).encode("utf-8"))
        tmp_path = self._temp_file()

        bridge.upload_workdrive_file(
            dc="eu", access_token="tok", parent_id="parent1", file_path=tmp_path
        )

        body = mock_api.call_args.kwargs["data"]
        self.assertIn(b'name="override-name-exist"', body)
        self.assertIn(b"false", body)

    def test_upload_rejects_file_above_documented_250mb_limit(self):
        tmp_path = self._temp_file()
        with patch("os.path.getsize", return_value=bridge.WORKDRIVE_MAX_UPLOAD_BYTES + 1):
            with self.assertRaises(ValueError) as ctx:
                bridge.upload_workdrive_file(
                    dc="eu", access_token="tok", parent_id="p1", file_path=tmp_path
                )
        self.assertIn("250 MB", str(ctx.exception))

    def test_extract_resource_id_from_upload_response_shapes(self):
        self.assertEqual(
            bridge.extract_workdrive_resource_id(
                {"data": [{"attributes": {"resource_id": "abc123"}}]}
            ),
            "abc123",
        )
        self.assertEqual(
            bridge.extract_workdrive_resource_id(
                {"data": {"attributes": {"RESOURCE_ID": "upper456"}}}
            ),
            "upper456",
        )
        self.assertEqual(
            bridge.extract_workdrive_resource_id({"data": [{"id": "fallback789"}]}),
            "fallback789",
        )
        self.assertIsNone(bridge.extract_workdrive_resource_id({"data": []}))
        self.assertIsNone(bridge.extract_workdrive_resource_id({}))

    @patch("bridge.api_request")
    def test_download_workdrive_file_uses_download_server(self, mock_api):
        mock_api.return_value = (200, b"downloaded WorkDrive bytes")

        data = bridge.download_workdrive_file("eu", "tok", "resource123")

        self.assertEqual(data, b"downloaded WorkDrive bytes")
        mock_api.assert_called_once_with(
            "https://download.zoho.eu/v1/workdrive/download/resource123",
            "tok",
            method="GET",
        )

    @patch("bridge.api_request")
    def test_download_workdrive_file_supports_version_query(self, mock_api):
        mock_api.return_value = (200, b"older version bytes")

        bridge.download_workdrive_file("com", "tok", "resource123", version="2")

        mock_api.assert_called_once_with(
            "https://download.zoho.com/v1/workdrive/download/resource123?version=2",
            "tok",
            method="GET",
        )

    @patch("bridge.download_workdrive_file")
    def test_verify_workdrive_file_match_and_mismatch(self, mock_download):
        raw_bytes = b"exact WorkDrive bytes"
        mock_download.return_value = raw_bytes

        verified, message = bridge.verify_workdrive_file(
            "eu", "tok", "resource123", bridge.sha256_bytes(raw_bytes)
        )
        self.assertTrue(verified)
        self.assertIn("Verified", message)

        verified, message = bridge.verify_workdrive_file(
            "eu", "tok", "resource123", bridge.sha256_bytes(b"different bytes")
        )
        self.assertFalse(verified)
        self.assertIn("mismatch", message)

    @patch("bridge.download_workdrive_file", side_effect=RuntimeError("HTTP 404"))
    def test_verify_workdrive_file_fails_when_read_back_fails(self, mock_download):
        verified, message = bridge.verify_workdrive_file(
            "eu", "tok", "resource123", "a" * 64
        )
        self.assertFalse(verified)
        self.assertIn("unable to read back", message)

    def test_parse_error_understands_workdrive_jsonapi_errors(self):
        body = json.dumps({"errors": [{"id": "F6003", "title": "Invalid Param found"}]}).encode()
        with self.assertRaises(RuntimeError) as ctx:
            bridge.parse_zoho_response(body, 400, "WorkDrive file upload")
        self.assertIn("Invalid Param found", str(ctx.exception))
        self.assertIn("F6003", str(ctx.exception))


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
            self.assertEqual(
                mock_up.call_args.kwargs["max_bytes"],
                bridge.DEFAULT_MAX_UPLOAD_BYTES["expense-receipt"],
            )
            mock_ver.assert_called_once()
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @patch("zoho_attach.verify_books_journal_attachment", return_value=(True, "Verified match"))
    @patch("zoho_attach.upload_books_journal_attachment", return_value={"code": 0, "message": "Uploaded"})
    @patch("zoho_attach.refresh_access_token", return_value="mock_access_token")
    @patch("zoho_attach.load_env")
    def test_successful_journal_upload_and_verification(self, mock_env, mock_tok, mock_up, mock_ver):
        mock_env.return_value = {
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "reftok",
            "dc": "eu",
            "books_org_id": "org123",
        }

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"Journal receipt bytes")
            tmp_path = tmp.name

        try:
            ret = zoho_attach.main([
                "--app", "books",
                "--target", "journal-attachment",
                "--id", "460000000038001",
                "--file", tmp_path,
            ])
            self.assertEqual(ret, 0)
            mock_up.assert_called_once()
            upload_kwargs = mock_up.call_args.kwargs
            self.assertEqual(upload_kwargs["journal_id"], "460000000038001")
            self.assertEqual(upload_kwargs["organization_id"], "org123")
            mock_ver.assert_called_once()
            verify_kwargs = mock_ver.call_args.kwargs
            self.assertEqual(verify_kwargs["journal_id"], "460000000038001")
            self.assertEqual(verify_kwargs["file_name"], Path(tmp_path).name)
            self.assertIn("expected_sha256", verify_kwargs)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_crm_requires_module_before_authentication(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"CRM bytes")
            tmp_path = tmp.name

        try:
            with patch("zoho_attach.load_env") as mock_env, \
                 patch("zoho_attach.refresh_access_token") as mock_token:
                mock_env.return_value = {
                    "client_id": "cid",
                    "client_secret": "csec",
                    "refresh_token": "reftok",
                    "dc": "eu",
                    "books_org_id": "",
                }
                ret = zoho_attach.main([
                    "--app", "crm",
                    "--target", "record-attachment",
                    "--id", "4876876000000376008",
                    "--file", tmp_path,
                ])
            self.assertEqual(ret, 1)
            mock_token.assert_not_called()
        finally:
            os.unlink(tmp_path)

    @patch("zoho_attach.verify_crm_record_attachment", return_value=(True, "Verified match"))
    @patch("zoho_attach.upload_crm_record_attachment", return_value={
        "data": [{"code": "SUCCESS", "details": {"id": "4876876000001021001"}}],
    })
    @patch("zoho_attach.refresh_access_token", return_value="mock_access_token")
    @patch("zoho_attach.load_env")
    def test_successful_crm_upload_does_not_require_organization_id(
        self, mock_env, mock_tok, mock_upload, mock_verify
    ):
        mock_env.return_value = {
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "reftok",
            "dc": "eu",
            "books_org_id": "",
        }

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"CRM document")
            tmp_path = tmp.name

        try:
            ret = zoho_attach.main([
                "--app", "crm",
                "--target", "record-attachment",
                "--module", "Deals",
                "--id", "4876876000000376008",
                "--file", tmp_path,
            ])
            self.assertEqual(ret, 0)
            mock_upload.assert_called_once()
            upload_kwargs = mock_upload.call_args.kwargs
            self.assertEqual(upload_kwargs["module"], "Deals")
            self.assertNotIn("organization_id", upload_kwargs)
            mock_verify.assert_called_once()
            verify_kwargs = mock_verify.call_args.kwargs
            self.assertEqual(verify_kwargs["attachment_id"], "4876876000001021001")
        finally:
            os.unlink(tmp_path)

    @patch("zoho_attach.verify_workdrive_file", return_value=(True, "Verified match"))
    @patch("zoho_attach.upload_workdrive_file", return_value={
        "data": [{"attributes": {"resource_id": "g4xh1aaaabbbbccccdddd100c5"}}],
    })
    @patch("zoho_attach.refresh_access_token", return_value="mock_access_token")
    @patch("zoho_attach.load_env")
    def test_successful_workdrive_file_upload(
        self, mock_env, mock_tok, mock_upload, mock_verify
    ):
        mock_env.return_value = {
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "reftok",
            "dc": "eu",
            "books_org_id": "",
        }

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"WorkDrive document")
            tmp_path = tmp.name

        try:
            ret = zoho_attach.main([
                "--app", "workdrive",
                "--target", "file-upload",
                "--id", "ly9zm0170fb40015f4e2",
                "--file", tmp_path,
            ])
            self.assertEqual(ret, 0)
            mock_upload.assert_called_once()
            upload_kwargs = mock_upload.call_args.kwargs
            self.assertEqual(upload_kwargs["parent_id"], "ly9zm0170fb40015f4e2")
            self.assertFalse(upload_kwargs["override_name_exist"])
            self.assertNotIn("organization_id", upload_kwargs)
            mock_verify.assert_called_once()
            verify_kwargs = mock_verify.call_args.kwargs
            self.assertEqual(verify_kwargs["resource_id"], "g4xh1aaaabbbbccccdddd100c5")
        finally:
            os.unlink(tmp_path)

    @patch("zoho_attach.verify_workdrive_file", return_value=(True, "Verified match"))
    @patch("zoho_attach.upload_workdrive_file", return_value={
        "data": [{"attributes": {"resource_id": "g4xh1aaaabbbbccccdddd100c5"}}],
    })
    @patch("zoho_attach.refresh_access_token", return_value="mock_access_token")
    @patch("zoho_attach.load_env")
    def test_workdrive_new_version_sets_override_flag(
        self, mock_env, mock_tok, mock_upload, mock_verify
    ):
        mock_env.return_value = {
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "reftok",
            "dc": "eu",
            "books_org_id": "",
        }

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"WorkDrive v2")
            tmp_path = tmp.name

        try:
            ret = zoho_attach.main([
                "--app", "workdrive",
                "--target", "new-version",
                "--id", "ly9zm0170fb40015f4e2",
                "--filename", "existing-report.pdf",
                "--file", tmp_path,
            ])
            self.assertEqual(ret, 0)
            upload_kwargs = mock_upload.call_args.kwargs
            self.assertTrue(upload_kwargs["override_name_exist"])
            self.assertEqual(upload_kwargs["filename"], "existing-report.pdf")
            mock_verify.assert_called_once()
        finally:
            os.unlink(tmp_path)

    @patch("zoho_attach.verify_workdrive_file")
    @patch("zoho_attach.upload_workdrive_file", return_value={"data": []})
    @patch("zoho_attach.refresh_access_token", return_value="mock_access_token")
    @patch("zoho_attach.load_env")
    def test_workdrive_upload_without_resource_id_is_failure(
        self, mock_env, mock_tok, mock_upload, mock_verify
    ):
        mock_env.return_value = {
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "reftok",
            "dc": "eu",
            "books_org_id": "",
        }

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"WorkDrive document")
            tmp_path = tmp.name

        try:
            ret = zoho_attach.main([
                "--app", "workdrive",
                "--target", "file-upload",
                "--id", "ly9zm0170fb40015f4e2",
                "--file", tmp_path,
            ])
            self.assertEqual(ret, 1)
            mock_verify.assert_not_called()
        finally:
            os.unlink(tmp_path)


class TestCliOnboarding(unittest.TestCase):
    """Test hardened onboarding input paths."""

    @patch("onboarding.update_env_file")
    @patch("onboarding.exchange_grant_token")
    @patch("onboarding.prompt_secret", return_value="my_client_sec")
    @patch("onboarding.prompt_input")
    def test_onboarding_flow(
        self, mock_input, mock_secret, mock_exchange, mock_update
    ):
        mock_input.side_effect = [
            "eu",               # DC
            "1000.CLIENTID",    # client_id
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
            mock_secret.assert_called_once_with("Enter Self Client Secret")
            mock_exchange.assert_called_once_with(
                client_id="1000.CLIENTID",
                client_secret="my_client_sec",
                code="1000.GRANTCODE",
                dc="eu",
            )
            mock_update.assert_called_once()

    def test_prompt_secret_uses_getpass(self):
        with patch("onboarding.getpass.getpass", return_value="hidden-value") as mock_getpass:
            self.assertEqual(onboarding.prompt_secret("Secret"), "hidden-value")
            mock_getpass.assert_called_once_with("Secret: ")

    def test_grant_code_file(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
            tmp.write("1000.FILE_GRANT\n")
            tmp_path = tmp.name
        try:
            args = onboarding.parse_args(["--grant-code-file", tmp_path])
            self.assertEqual(onboarding.read_grant_code(args), "1000.FILE_GRANT")
        finally:
            os.unlink(tmp_path)

    def test_grant_code_stdin(self):
        args = onboarding.parse_args(["--grant-code-file", "-"])
        with patch("onboarding.sys.stdin", io.StringIO("1000.STDIN_GRANT\n")):
            self.assertEqual(onboarding.read_grant_code(args), "1000.STDIN_GRANT")

    def test_grant_code_sources_are_mutually_exclusive(self):
        args = onboarding.parse_args([
            "--grant-code", "inline",
            "--grant-code-file", "grant.txt",
        ])
        with self.assertRaises(ValueError):
            onboarding.read_grant_code(args)


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

        with open(self.cache, encoding="utf-8") as fh:
            raw = fh.read()
        self.assertNotIn("csec", raw)
        self.assertNotIn("rtok", raw)
        self.assertNotIn("cid", raw)

    def test_expired_entry_triggers_refresh(self):
        payload = {"access_token": "tok_a", "expires_in": 3600}
        with patch("urllib.request.urlopen", return_value=self._fake_response(payload)):
            bridge.refresh_access_token("cid", "csec", "rtok", "eu")

        with open(self.cache, encoding="utf-8") as fh:
            entries = json.loads(fh.read())
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

class TestOrganizationAndPortalDiscovery(unittest.TestCase):
    """Test Books organization and Projects portal discovery helpers."""

    def test_projects_base_url(self):
        expected = {
            "eu": "https://projectsapi.zoho.eu",
            "com": "https://projectsapi.zoho.com",
            "ca": "https://projectsapi.zohocloud.ca",
            "in": "https://projectsapi.zoho.in",
        }
        for dc, url in expected.items():
            self.assertEqual(bridge.projects_base_url(dc), url)
        with self.assertRaises(ValueError):
            bridge.projects_base_url("bad_dc")

    def test_parse_books_6024_organizations(self):
        body = json.dumps({
            "code": 6024,
            "message": "This user belongs to multiple organizations",
            "organizations": [
                {
                    "organization_id": "1001",
                    "name": "Org One",
                    "is_default_org": True,
                },
                {
                    "organization_id": "1002",
                    "name": "Org Two",
                    "is_default_org": False,
                },
            ],
        }).encode("utf-8")
        parsed = bridge.parse_books_6024_organizations(body)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["organization_id"], "1001")
        self.assertEqual(parsed[0]["name"], "Org One")
        self.assertTrue(parsed[0]["is_default_org"])
        self.assertEqual(parsed[1]["organization_id"], "1002")

    def test_parse_books_6024_invalid_body_returns_empty(self):
        self.assertEqual(bridge.parse_books_6024_organizations(b"not json"), [])
        self.assertEqual(bridge.parse_books_6024_organizations(b"{}"), [])

    def test_parse_books_6024_error_info_shape(self):
        body = json.dumps({
            "code": 6024,
            "message": "This user belongs to multiple organizations",
            "error_info": {
                "organizations": [
                    {"id": "2001", "organization_name": "Nested Org", "is_default_org": False}
                ]
            },
        }).encode("utf-8")
        parsed = bridge.parse_books_6024_organizations(body)
        self.assertEqual(parsed[0]["organization_id"], "2001")
        self.assertEqual(parsed[0]["name"], "Nested Org")

    @patch("bridge.api_request")
    def test_list_books_organizations_success(self, mock_api):
        mock_api.return_value = (200, json.dumps({
            "code": 0,
            "message": "success",
            "organizations": [
                {
                    "organization_id": "123456",
                    "name": "SprintCX GmbH",
                    "is_default_org": True,
                    "currency_code": "EUR",
                    "time_zone": "Europe/Berlin",
                }
            ],
        }).encode("utf-8"))

        orgs = bridge.list_books_organizations("eu", "mock_access_token")
        self.assertEqual(len(orgs), 1)
        self.assertEqual(orgs[0]["organization_id"], "123456")
        self.assertEqual(orgs[0]["name"], "SprintCX GmbH")
        self.assertTrue(orgs[0]["is_default_org"])
        mock_api.assert_called_once_with(
            "https://www.zohoapis.eu/books/v3/organizations",
            "mock_access_token",
            method="GET",
        )

    @patch("bridge.api_request")
    def test_list_projects_portals_success(self, mock_api):
        mock_api.return_value = (200, json.dumps([
            {
                "id": "647154632",
                "portal_name": "sprintcx",
                "is_default_portal": True,
                "project_plan": "Enterprise",
            }
        ]).encode("utf-8"))

        portals = bridge.list_projects_portals("eu", "mock_access_token")
        self.assertEqual(len(portals), 1)
        self.assertEqual(portals[0]["portal_id"], "647154632")
        self.assertEqual(portals[0]["name"], "sprintcx")
        self.assertTrue(portals[0]["is_default_portal"])
        mock_api.assert_called_once_with(
            "https://projectsapi.zoho.eu/api/v3/portals",
            "mock_access_token",
            method="GET",
        )


class TestDiscoverCli(unittest.TestCase):
    """Test scripts/discover.py CLI execution."""

    @patch("discover.list_books_organizations")
    @patch("discover.refresh_access_token", return_value="tok123")
    @patch("discover.load_env")
    def test_discover_books_organizations_cli(self, mock_env, mock_tok, mock_list):
        import discover
        mock_env.return_value = {
            "client_id": "cid", "client_secret": "csec", "refresh_token": "reftok", "dc": "eu"
        }
        mock_list.return_value = [
            {"organization_id": "111", "name": "OrgA", "is_default_org": True, "currency_code": "EUR", "time_zone": "CET"}
        ]
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            ret = discover.main(["books-organizations"])
            self.assertEqual(ret, 0)
            out = mock_out.getvalue()
            self.assertIn("111", out)
            self.assertIn("OrgA", out)

    @patch("discover.list_projects_portals")
    @patch("discover.refresh_access_token", return_value="tok123")
    @patch("discover.load_env")
    def test_discover_projects_portals_cli_json(self, mock_env, mock_tok, mock_list):
        import discover
        mock_env.return_value = {
            "client_id": "cid", "client_secret": "csec", "refresh_token": "reftok", "dc": "eu"
        }
        mock_list.return_value = [
            {"portal_id": "999", "name": "PortalX", "is_default_portal": False, "project_plan": "Free"}
        ]
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            ret = discover.main(["projects-portals", "--json"])
            self.assertEqual(ret, 0)
            out = mock_out.getvalue()
            data = json.loads(out)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["portal_id"], "999")


class TestProjectsOperationsAndVerification(unittest.TestCase):
    """Zoho Projects task and comment attachment upload plus SHA-256 read-back."""

    def _temp_file(self, content: bytes = b"Projects binary payload", suffix: str = ".pdf") -> str:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            self.addCleanup(lambda p=tmp.name: os.path.exists(p) and os.unlink(p))
            return tmp.name

    def test_projects_urls_and_id_validation(self):
        url = bridge.projects_task_attachments_url("eu", "2063927", "170876000004921003", "170876000004922138")
        self.assertEqual(
            url,
            "https://projectsapi.zoho.eu/restapi/portal/2063927/projects/170876000004921003/tasks/170876000004922138/attachments/",
        )
        comments = bridge.projects_task_comments_url("com", "1", "2", "3")
        self.assertEqual(
            comments,
            "https://projectsapi.zoho.com/restapi/portal/1/projects/2/tasks/3/comments/",
        )
        with self.assertRaises(ValueError):
            bridge.projects_task_attachments_url("eu", "portal/1", "2", "3")

    def test_projects_accepts_any_extension_but_requires_one(self):
        for name in ["doc.pdf", "archive.zip", "notes.txt"]:
            for target in ("task-attachment", "comment-attachment"):
                self.assertTrue(bridge.validate_file_extension(name, target).startswith("."))
        with self.assertRaises(ValueError):
            bridge.validate_file_extension("extensionless", "task-attachment")

    @patch("bridge.api_request")
    def test_upload_projects_task_attachment_posts_uploaddoc(self, mock_api):
        mock_api.return_value = (200, json.dumps([{
            "FILENAME": "spec.pdf",
            "RESOURCE_ID": "033zqca7d98669ef541348a5d2ded5d44ff3d",
            "DOWNLOAD_URL": "https://download.zoho.com/paramdownloadservlet?x-service=EX",
        }]).encode("utf-8"))
        tmp_path = self._temp_file()
        response = bridge.upload_projects_task_attachment(
            dc="eu",
            access_token="tok",
            portal_id="2063927",
            project_id="170876000004921003",
            task_id="170876000004922138",
            file_path=tmp_path,
        )
        self.assertEqual(response[0]["RESOURCE_ID"], "033zqca7d98669ef541348a5d2ded5d44ff3d")
        args, kwargs = mock_api.call_args
        self.assertTrue(args[0].endswith("/attachments/"))
        self.assertEqual(kwargs["method"], "POST")
        self.assertIn(b'name="uploaddoc"', kwargs["data"])
        self.assertIn(b"Projects binary payload", kwargs["data"])

    @patch("bridge.api_request")
    def test_upload_projects_comment_attachment_posts_content_and_uploaddoc(self, mock_api):
        mock_api.return_value = (201, json.dumps({
            "comments": [{"id": 57000001149011, "content": "note"}]
        }).encode("utf-8"))
        tmp_path = self._temp_file()
        response = bridge.upload_projects_comment_attachment(
            dc="eu",
            access_token="tok",
            portal_id="2063927",
            project_id="170876000004921003",
            task_id="170876000004922138",
            file_path=tmp_path,
            comment="Setup Demo Video",
        )
        self.assertEqual(bridge.extract_projects_comment_id(response), "57000001149011")
        body = mock_api.call_args.kwargs["data"]
        self.assertIn(b'name="uploaddoc"', body)
        self.assertIn(b'name="content"', body)
        self.assertIn(b"Setup Demo Video", body)
        self.assertTrue(mock_api.call_args.args[0].endswith("/comments/"))

    @patch("bridge.download_projects_attachment", return_value=b"Projects binary payload")
    @patch("bridge.list_projects_task_attachments")
    def test_verify_projects_task_attachment_match(self, mock_list, mock_download):
        mock_list.return_value = [{
            "resource_id": "res1",
            "filename": "spec.pdf",
            "download_url": "https://download.zoho.com/file",
        }]
        verified, message = bridge.verify_projects_task_attachment(
            "eu", "tok", "1", "2", "3", "spec.pdf",
            bridge.sha256_bytes(b"Projects binary payload"),
            resource_id="res1",
        )
        self.assertTrue(verified)
        self.assertIn("Verified", message)
        mock_download.assert_called_once()

    @patch("bridge.list_projects_task_attachments", return_value=[])
    def test_verify_projects_task_attachment_missing(self, mock_list):
        verified, message = bridge.verify_projects_task_attachment(
            "eu", "tok", "1", "2", "3", "missing.pdf", "a" * 64
        )
        self.assertFalse(verified)
        self.assertIn("not found", message)

    @patch("bridge.verify_projects_task_attachment", return_value=(True, "Verified: SHA-256 match"))
    @patch("bridge.list_projects_task_comments")
    def test_verify_projects_comment_attachment_requires_comment(self, mock_comments, mock_verify):
        mock_comments.return_value = [{"id": "57000001149011", "content": "note"}]
        verified, _ = bridge.verify_projects_comment_attachment(
            "eu", "tok", "1", "2", "3", "spec.pdf", "a" * 64, comment_id="57000001149011"
        )
        self.assertTrue(verified)
        mock_verify.assert_called_once()

    @patch("bridge.list_projects_task_comments", return_value=[{"id": "other"}])
    def test_verify_projects_comment_attachment_missing_comment(self, mock_comments):
        verified, message = bridge.verify_projects_comment_attachment(
            "eu", "tok", "1", "2", "3", "spec.pdf", "a" * 64, comment_id="57000001149011"
        )
        self.assertFalse(verified)
        self.assertIn("comment was not found", message)


class TestCliProjectsAttach(unittest.TestCase):
    """CLI coverage for --app projects."""

    def test_projects_requires_project_id_before_authentication(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"Projects bytes")
            tmp_path = tmp.name
        try:
            with patch("zoho_attach.load_env") as mock_env,                  patch("zoho_attach.refresh_access_token") as mock_token:
                mock_env.return_value = {
                    "client_id": "cid",
                    "client_secret": "csec",
                    "refresh_token": "reftok",
                    "dc": "eu",
                    "books_org_id": "",
                    "projects_portal_id": "2063927",
                }
                ret = zoho_attach.main([
                    "--app", "projects",
                    "--target", "task-attachment",
                    "--id", "170876000004922138",
                    "--file", tmp_path,
                ])
            self.assertEqual(ret, 1)
            mock_token.assert_not_called()
        finally:
            os.unlink(tmp_path)

    @patch("zoho_attach.verify_projects_task_attachment", return_value=(True, "Verified match"))
    @patch("zoho_attach.upload_projects_task_attachment", return_value=[{
        "FILENAME": "spec.pdf",
        "RESOURCE_ID": "res1",
        "DOWNLOAD_URL": "https://download.zoho.com/file",
    }])
    @patch("zoho_attach.refresh_access_token", return_value="mock_access_token")
    @patch("zoho_attach.load_env")
    def test_successful_projects_task_upload(self, mock_env, mock_tok, mock_upload, mock_verify):
        mock_env.return_value = {
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "reftok",
            "dc": "eu",
            "books_org_id": "",
            "projects_portal_id": "2063927",
        }
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"Projects document")
            tmp_path = tmp.name
        try:
            ret = zoho_attach.main([
                "--app", "projects",
                "--target", "task-attachment",
                "--id", "170876000004922138",
                "--project-id", "170876000004921003",
                "--file", tmp_path,
            ])
            self.assertEqual(ret, 0)
            mock_upload.assert_called_once()
            kwargs = mock_upload.call_args.kwargs
            self.assertEqual(kwargs["portal_id"], "2063927")
            self.assertEqual(kwargs["project_id"], "170876000004921003")
            self.assertEqual(kwargs["task_id"], "170876000004922138")
            mock_verify.assert_called_once()
        finally:
            os.unlink(tmp_path)

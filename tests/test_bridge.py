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
            mock_ver.assert_called_once()
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

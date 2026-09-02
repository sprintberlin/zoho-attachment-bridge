"""
Zoho Attachment Bridge — core library.

Stdlib-only implementation for Self Client OAuth token refresh,
multipart/form-data upload, download, SHA-256 verification, and error handling.
No third-party packages.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Data-center mapping
# ---------------------------------------------------------------------------

DC_MAP: Dict[str, str] = {
    "eu": "zoho.eu",
    "com": "zoho.com",
    "in": "zoho.in",
    "com.au": "zoho.com.au",
    "jp": "zoho.jp",
    "ca": "zohocloud.ca",
    "sa": "zoho.sa",
    "com.cn": "zoho.com.cn",
}

# API domain per data center. Zoho API hosts always live under zohoapis.<tld>,
# which differs from the accounts host for Canada (accounts.zohocloud.ca but
# www.zohoapis.ca).
API_DC_MAP: Dict[str, str] = {
    "eu": "zohoapis.eu",
    "com": "zohoapis.com",
    "in": "zohoapis.in",
    "com.au": "zohoapis.com.au",
    "jp": "zohoapis.jp",
    "ca": "zohoapis.ca",
    "sa": "zohoapis.sa",
    "com.cn": "zohoapis.com.cn",
}

# ---------------------------------------------------------------------------
# Extension allowlists per target
# ---------------------------------------------------------------------------

# Zoho Books API v3 documented allowlists:
# - Expense receipts: gif, png, jpeg, jpg, bmp, pdf, xls, xlsx, doc, docx
#   (https://www.zoho.com/books/api/v3/expenses/ — "Add receipt to an expense")
# - Bill attachments: gif, png, jpeg, jpg, bmp, pdf
#   (https://www.zoho.com/books/api/v3/bills/ — "Add attachment to a bill")
EXPENSE_RECEIPT_EXTENSIONS: Set[str] = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp",
    ".pdf", ".xls", ".xlsx", ".doc", ".docx",
}

BILL_ATTACHMENT_EXTENSIONS: Set[str] = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".pdf",
}

_TARGET_EXTENSIONS: Dict[str, Set[str]] = {
    "expense-receipt": EXPENSE_RECEIPT_EXTENSIONS,
    "bill-attachment": BILL_ATTACHMENT_EXTENSIONS,
}

# ---------------------------------------------------------------------------
# MIME type mapping
# ---------------------------------------------------------------------------

_MIME_MAP: Dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".rtf": "application/rtf",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
}


# ---------------------------------------------------------------------------
# Env file parser & writer (safe, preserves comments/order, sets mode 0600)
# ---------------------------------------------------------------------------

def parse_env_content(content: str) -> Dict[str, str]:
    """Parse KEY=VALUE pairs from string content."""
    result: Dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key:
            result[key] = value
    return result


def parse_env_file(path: Path) -> Dict[str, str]:
    """Parse KEY=VALUE pairs from an env file if it exists."""
    if not path.is_file():
        return {}
    try:
        content = path.read_text(encoding="utf-8")
        return parse_env_content(content)
    except Exception:
        return {}


def update_env_file(path: Path, updates: Dict[str, str]) -> None:
    """
    Safely update or add keys in an env file while preserving unrelated lines,
    comments, blank lines, and formatting. Ensures file permissions are mode 0600.
    """
    lines: List[str] = []
    if path.is_file():
        try:
            content = path.read_text(encoding="utf-8")
            lines = content.splitlines()
        except Exception:
            lines = []

    remaining_keys = set(updates.keys())
    new_lines: List[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        if "=" in line:
            key, _, _ = line.partition("=")
            key = key.strip()
            if key in updates:
                new_val = updates[key]
                # Quote value if it contains spaces, special characters or equals
                if re.search(r'[\s#\'"=]', new_val) or not new_val:
                    escaped = new_val.replace('"', '\\"')
                    new_lines.append(f'{key}="{escaped}"')
                else:
                    new_lines.append(f"{key}={new_val}")
                remaining_keys.discard(key)
                continue
        new_lines.append(line)

    # Append any keys that were not already present
    if new_lines and new_lines[-1] != "":
        new_lines.append("")

    for key in sorted(remaining_keys):
        new_val = updates[key]
        if re.search(r'[\s#\'"=]', new_val) or not new_val:
            escaped = new_val.replace('"', '\\"')
            new_lines.append(f'{key}="{escaped}"')
        else:
            new_lines.append(f"{key}={new_val}")

    parent = path.parent
    if parent and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)

    output = "\n".join(new_lines)
    if output and not output.endswith("\n"):
        output += "\n"

    # Write securely with mode 0600
    # Open with os.open using O_CREAT | O_WRONLY | O_TRUNC and mode 0600
    fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, output.encode("utf-8"))
    finally:
        os.close(fd)

    # In case the file already existed with different permissions, force 0600
    try:
        os.chmod(str(path), 0o600)
    except Exception:
        pass


def load_env(profile: Optional[str] = None) -> Dict[str, str]:
    """
    Load environment variables with precedence:
      1. Current process env (highest)
      2. Local .env in cwd
      3. ~/.openclaw/.env (lowest file)

    Named profiles use ZOHO_BRIDGE_<PROFILE>_<KEY>.
    Default uses ZOHO_BRIDGE_<KEY>.
    """
    prefix = "ZOHO_BRIDGE"
    if profile:
        prefix = f"ZOHO_BRIDGE_{profile.upper()}"

    file_env: Dict[str, str] = {}
    home = Path.home()
    file_env.update(parse_env_file(home / ".openclaw" / ".env"))
    file_env.update(parse_env_file(Path.cwd() / ".env"))

    def _get(key: str) -> Optional[str]:
        full_key = f"{prefix}_{key}"
        return os.environ.get(full_key) or file_env.get(full_key)

    client_id = _get("CLIENT_ID")
    client_secret = _get("CLIENT_SECRET")
    refresh_token = _get("REFRESH_TOKEN")
    dc = _get("DC")
    books_org_id = _get("BOOKS_ORG_ID")

    missing = []
    if not client_id:
        missing.append(f"{prefix}_CLIENT_ID")
    if not client_secret:
        missing.append(f"{prefix}_CLIENT_SECRET")
    if not refresh_token:
        missing.append(f"{prefix}_REFRESH_TOKEN")
    if not dc:
        missing.append(f"{prefix}_DC")
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "dc": dc.lower().strip(),
        "books_org_id": books_org_id or "",
    }


# ---------------------------------------------------------------------------
# Data center resolution
# ---------------------------------------------------------------------------

def resolve_dc(dc: str) -> str:
    """Resolve data center shorthand to the domain suffix."""
    normalized = dc.lower().strip()
    if normalized not in DC_MAP:
        raise ValueError(
            f"Unknown data center '{dc}'. "
            f"Valid options: {', '.join(sorted(DC_MAP.keys()))}"
        )
    return DC_MAP[normalized]


def accounts_base_url(dc: str) -> str:
    """Return the accounts OAuth base URL."""
    return f"https://accounts.{resolve_dc(dc)}"


def books_base_url(dc: str) -> str:
    """Return the Zoho Books API v3 base URL (e.g. https://www.zohoapis.eu/books/v3)."""
    resolve_dc(dc)  # validate
    return f"https://www.{API_DC_MAP[dc.lower().strip()]}/books/v3"


# ---------------------------------------------------------------------------
# OAuth token exchange & refresh
# ---------------------------------------------------------------------------

def exchange_grant_token(
    client_id: str,
    client_secret: str,
    code: str,
    dc: str,
) -> Dict[str, Any]:
    """
    Exchange an authorization grant token for access and refresh tokens.
    Returns the parsed JSON response dictionary.
    """
    url = f"{accounts_base_url(dc)}/oauth/v2/token"
    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            body = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Grant token exchange failed: HTTP {exc.code} — {err_text}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Grant token exchange failed: network error — {exc.reason}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Grant token exchange failed: invalid JSON response") from exc

    if "error" in body:
        err = body.get("error")
        desc = body.get("error_description")
        err_msg = f"{err} — {desc}" if err and desc else (desc or err or "unknown error")
        raise RuntimeError(f"OAuth grant token error: {err_msg}")

    if not body.get("refresh_token"):
        raise RuntimeError(
            "OAuth grant token response did not contain a refresh_token"
        )

    return body


def refresh_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    dc: str,
    use_cache: bool = True,
) -> str:
    """
    Exchange a refresh token for a fresh short-lived access token.

    Access tokens are cached on disk (mode 0600) until shortly before they
    expire. Zoho rate-limits the token endpoint aggressively, so refreshing on
    every invocation will eventually fail with "too many requests".

    Never logs or exposes the client secret or tokens.
    """
    if use_cache:
        cached = _read_cached_token(client_id, refresh_token, dc)
        if cached:
            return cached

    url = f"{accounts_base_url(dc)}/oauth/v2/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            body = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Token refresh failed: HTTP {exc.code} — {err_text}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Token refresh failed: network error — {exc.reason}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Token refresh failed: invalid JSON response") from exc

    if "error" in body:
        err = body.get("error")
        desc = body.get("error_description")
        err_msg = f"{err} — {desc}" if err and desc else (desc or err or "unknown error")
        raise RuntimeError(f"OAuth refresh error: {err_msg}")

    access_token = body.get("access_token")
    if not access_token:
        raise RuntimeError("Token refresh response missing access_token")

    if use_cache:
        try:
            expires_in = int(body.get("expires_in", 3600))
        except (TypeError, ValueError):
            expires_in = 3600
        _write_cached_token(client_id, refresh_token, dc, access_token, expires_in)

    return access_token


# ---------------------------------------------------------------------------
# Access token cache
# ---------------------------------------------------------------------------

# Refresh this many seconds before the token actually expires.
TOKEN_EXPIRY_MARGIN_SECONDS = 300


def token_cache_path() -> Path:
    """Location of the on-disk access token cache."""
    override = os.environ.get("ZOHO_BRIDGE_TOKEN_CACHE")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "zoho-attachment-bridge" / "tokens.json"


def _cache_key(client_id: str, refresh_token: str, dc: str) -> str:
    """Opaque cache key. Secrets are hashed, never stored in clear text."""
    digest = hashlib.sha256(
        f"{client_id}:{refresh_token}:{dc}".encode("utf-8")
    ).hexdigest()
    return digest


def _read_cached_token(client_id: str, refresh_token: str, dc: str) -> Optional[str]:
    """Return a cached access token if it is still valid, else None."""
    path = token_cache_path()
    if not path.is_file():
        return None
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(entries, dict):
        return None

    entry = entries.get(_cache_key(client_id, refresh_token, dc))
    if not isinstance(entry, dict):
        return None

    token = entry.get("access_token")
    expires_at = entry.get("expires_at")
    if not token or not isinstance(expires_at, (int, float)):
        return None
    if time.time() >= float(expires_at):
        return None
    return str(token)


def _write_cached_token(
    client_id: str,
    refresh_token: str,
    dc: str,
    access_token: str,
    expires_in: int,
) -> None:
    """Persist an access token with mode 0600. Failures are non-fatal."""
    path = token_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    entries: Dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                entries = loaded
        except (OSError, json.JSONDecodeError):
            entries = {}

    now = time.time()
    entries[_cache_key(client_id, refresh_token, dc)] = {
        "access_token": access_token,
        "expires_at": now + max(0, expires_in - TOKEN_EXPIRY_MARGIN_SECONDS),
    }

    # Drop stale entries so the cache does not grow without bound.
    entries = {
        k: v
        for k, v in entries.items()
        if isinstance(v, dict)
        and isinstance(v.get("expires_at"), (int, float))
        and float(v["expires_at"]) > now
    }

    try:
        fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(entries).encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(str(path), 0o600)
    except OSError:
        return


# ---------------------------------------------------------------------------
# File extension & MIME validation
# ---------------------------------------------------------------------------

def allowed_extensions(target: str) -> Set[str]:
    """Return allowed extensions for the given target."""
    if target not in _TARGET_EXTENSIONS:
        raise ValueError(
            f"Unknown target '{target}'. "
            f"Valid targets: {', '.join(sorted(_TARGET_EXTENSIONS.keys()))}"
        )
    return _TARGET_EXTENSIONS[target]


def validate_file_extension(file_path: str, target: str) -> str:
    """
    Validate that the file's extension is in the target allowlist.
    Returns normalized lowercase extension (e.g. '.pdf').
    """
    ext = Path(file_path).suffix.lower()
    allowed = allowed_extensions(target)
    if ext not in allowed:
        raise ValueError(
            f"File extension '{ext}' is not allowed for '{target}'. "
            f"Allowed extensions: {', '.join(sorted(allowed))}"
        )
    return ext


def guess_mime_type(file_path: str) -> str:
    """Guess MIME type based on file extension."""
    ext = Path(file_path).suffix.lower()
    return _MIME_MAP.get(ext, "application/octet-stream")


# ---------------------------------------------------------------------------
# Checksum computation
# ---------------------------------------------------------------------------

def sha256_file(file_path: str) -> str:
    """Compute SHA-256 hex digest of a local file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Compute SHA-256 hex digest of a byte sequence."""
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Multipart/form-data builder (RFC 2388 / RFC 7578 compliant, stdlib only)
# ---------------------------------------------------------------------------

def build_multipart_body(
    file_path: str,
    field_name: str,
    extra_fields: Optional[Dict[str, str]] = None,
) -> Tuple[bytes, str]:
    """
    Build a standard multipart/form-data body containing a binary file
    and optional text fields.
    Returns (body_bytes, Content-Type header string).
    """
    boundary = f"----ZohoBridgeBoundary{hashlib.md5(f'{time.time()}_{file_path}'.encode()).hexdigest()[:16]}"
    filename = Path(file_path).name
    mime_type = guess_mime_type(file_path)

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    parts: List[bytes] = []

    # Extra form fields if any
    if extra_fields:
        for k, v in extra_fields.items():
            parts.append(f"--{boundary}\r\n".encode("utf-8"))
            parts.append(
                f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode("utf-8")
            )
            parts.append(f"{v}\r\n".encode("utf-8"))

    # File field
    parts.append(f"--{boundary}\r\n".encode("utf-8"))
    parts.append(
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode("utf-8")
    )
    parts.append(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
    parts.append(file_bytes)
    parts.append(b"\r\n")

    # Closing boundary
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))

    body = b"".join(parts)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


# ---------------------------------------------------------------------------
# HTTP request with 429 backoff handling
# ---------------------------------------------------------------------------

def _execute_http(
    url: str,
    headers: Dict[str, str],
    data: Optional[bytes] = None,
    method: Optional[str] = None,
    timeout: int = 60,
) -> Tuple[int, bytes, Dict[str, str]]:
    """Low-level single HTTP invocation."""
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, body, resp_headers
    except urllib.error.HTTPError as exc:
        body = exc.read()
        resp_headers = {k.lower(): v for k, v in exc.headers.items()}
        return exc.code, body, resp_headers
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error: {exc.reason}") from exc


def api_request(
    url: str,
    access_token: str,
    data: Optional[bytes] = None,
    content_type: Optional[str] = None,
    method: Optional[str] = None,
    max_retries: int = 3,
    timeout: int = 60,
) -> Tuple[int, bytes]:
    """
    Authenticated HTTP request with 429 Retry-After exponential backoff.
    Never logs access tokens or secrets.
    """
    headers: Dict[str, str] = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
    }
    if content_type:
        headers["Content-Type"] = content_type

    attempt = 0
    while True:
        status, body, resp_headers = _execute_http(
            url, headers, data=data, method=method, timeout=timeout
        )

        if status == 429:
            attempt += 1
            if attempt > max_retries:
                err_text = body.decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"HTTP 429: Rate limit exceeded after {max_retries} retries — {err_text}"
                )
            retry_after = resp_headers.get("retry-after")
            wait_time = 2 ** attempt
            if retry_after:
                try:
                    wait_time = max(1, int(retry_after))
                except ValueError:
                    pass
            time.sleep(wait_time)
            continue

        return status, body


def parse_zoho_response(body: bytes, status: int, action_context: str) -> Dict[str, Any]:
    """Parse and validate JSON response from Zoho API."""
    text = body.decode("utf-8", errors="replace")

    if status >= 400:
        # Check if error message is formatted as JSON
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "message" in data:
                raise RuntimeError(
                    f"{action_context} failed (HTTP {status}): {data.get('message')} (code: {data.get('code')})"
                )
        except json.JSONDecodeError:
            pass
        raise RuntimeError(f"{action_context} failed: HTTP {status} — {text}")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{action_context} failed: Invalid JSON response — {text[:400]}"
        ) from exc

    if isinstance(data, dict):
        code = data.get("code")
        if code is not None and code != 0:
            msg = data.get("message", "unknown error")
            raise RuntimeError(f"{action_context} error (Zoho code {code}): {msg}")

    return data


# ---------------------------------------------------------------------------
# Books API Upload & Read-Back
# ---------------------------------------------------------------------------

def upload_books_expense_receipt(
    dc: str,
    access_token: str,
    organization_id: str,
    expense_id: str,
    file_path: str,
) -> Dict[str, Any]:
    """
    Upload expense receipt using multipart/form-data.
    POST /api/v3/expenses/{expense_id}/receipt?organization_id={org_id}
    """
    validate_file_extension(file_path, "expense-receipt")
    body, content_type = build_multipart_body(file_path, field_name="receipt")
    url = (
        f"{books_base_url(dc)}/expenses/{expense_id}/receipt"
        f"?organization_id={organization_id}"
    )
    status, resp_bytes = api_request(
        url, access_token, data=body, content_type=content_type, method="POST"
    )
    return parse_zoho_response(resp_bytes, status, "Expense receipt upload")


def upload_books_bill_attachment(
    dc: str,
    access_token: str,
    organization_id: str,
    bill_id: str,
    file_path: str,
) -> Dict[str, Any]:
    """
    Upload bill attachment using multipart/form-data.
    POST /api/v3/bills/{bill_id}/attachment?organization_id={org_id}
    """
    validate_file_extension(file_path, "bill-attachment")
    body, content_type = build_multipart_body(file_path, field_name="attachment")
    url = (
        f"{books_base_url(dc)}/bills/{bill_id}/attachment"
        f"?organization_id={organization_id}"
    )
    status, resp_bytes = api_request(
        url, access_token, data=body, content_type=content_type, method="POST"
    )
    return parse_zoho_response(resp_bytes, status, "Bill attachment upload")


def download_books_expense_receipt(
    dc: str,
    access_token: str,
    organization_id: str,
    expense_id: str,
) -> bytes:
    """
    Download the receipt of an expense.
    GET /api/v3/expenses/{expense_id}/receipt?organization_id={org_id}
    """
    url = (
        f"{books_base_url(dc)}/expenses/{expense_id}/receipt"
        f"?organization_id={organization_id}"
    )
    status, body = api_request(url, access_token, method="GET")
    if status >= 400:
        err_text = body.decode("utf-8", errors="replace")
        raise RuntimeError(f"Failed to download expense receipt: HTTP {status} — {err_text}")
    return body


def download_books_bill_attachment(
    dc: str,
    access_token: str,
    organization_id: str,
    bill_id: str,
) -> bytes:
    """
    Download the attachment of a bill.
    GET /api/v3/bills/{bill_id}/attachment?organization_id={org_id}
    """
    url = (
        f"{books_base_url(dc)}/bills/{bill_id}/attachment"
        f"?organization_id={organization_id}"
    )
    status, body = api_request(url, access_token, method="GET")
    if status >= 400:
        err_text = body.decode("utf-8", errors="replace")
        raise RuntimeError(f"Failed to download bill attachment: HTTP {status} — {err_text}")
    return body


def verify_books_expense_receipt(
    dc: str,
    access_token: str,
    organization_id: str,
    expense_id: str,
    expected_sha256: str,
) -> Tuple[bool, str]:
    """
    Read back and verify the uploaded expense receipt by downloading it
    and comparing the SHA-256 hash.
    Returns (success_bool, message).
    """
    try:
        downloaded = download_books_expense_receipt(
            dc, access_token, organization_id, expense_id
        )
    except Exception as exc:
        return False, f"Verification failed: unable to read back receipt ({exc})"

    downloaded_sha256 = sha256_bytes(downloaded)
    if downloaded_sha256 == expected_sha256:
        return True, f"Verified: SHA-256 match ({downloaded_sha256})"
    return False, (
        f"Verification failed: SHA-256 mismatch. "
        f"Expected {expected_sha256}, got {downloaded_sha256}"
    )


def verify_books_bill_attachment(
    dc: str,
    access_token: str,
    organization_id: str,
    bill_id: str,
    expected_sha256: str,
) -> Tuple[bool, str]:
    """
    Read back and verify the uploaded bill attachment by downloading it
    and comparing the SHA-256 hash.
    Returns (success_bool, message).
    """
    try:
        downloaded = download_books_bill_attachment(
            dc, access_token, organization_id, bill_id
        )
    except Exception as exc:
        return False, f"Verification failed: unable to read back attachment ({exc})"

    downloaded_sha256 = sha256_bytes(downloaded)
    if downloaded_sha256 == expected_sha256:
        return True, f"Verified: SHA-256 match ({downloaded_sha256})"
    return False, (
        f"Verification failed: SHA-256 mismatch. "
        f"Expected {expected_sha256}, got {downloaded_sha256}"
    )

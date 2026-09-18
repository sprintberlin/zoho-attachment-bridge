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

# WorkDrive serves file downloads from a dedicated host per data center, which
# is neither the accounts host nor the zohoapis host.
# https://www.zoho.com/workdrive/developer/docs/api/v1/download-file.html
WORKDRIVE_DOWNLOAD_DC_MAP: Dict[str, str] = {
    "eu": "download.zoho.eu",
    "com": "download.zoho.com",
    "in": "download.zoho.in",
    "com.au": "download.zoho.com.au",
    "jp": "download.zoho.jp",
    "ca": "download.zohocloud.ca",
    "sa": "files.zoho.sa",
    "com.cn": "download.zoho.com.cn",
}

# Documented maximum for the multipart WorkDrive upload endpoint. Larger files
# need the separate stream upload endpoint, which this bridge does not
# implement.
# https://www.zoho.com/workdrive/developer/docs/api/v1/upload-file.html
WORKDRIVE_MAX_UPLOAD_BYTES: int = 250 * 1024 * 1024

# Documented per-target upload size limits. Override with
# ZOHO_BRIDGE_MAX_BYTES_<TARGET> where TARGET uses underscores
# (EXPENSE_RECEIPT, BILL_ATTACHMENT, RECORD_ATTACHMENT, FILE_UPLOAD,
# NEW_VERSION).
# Expense receipts: Zoho Books Welcome Guide, "Maximum file size allowed is 7MB"
#   https://www.zoho.com/us/books/welcome-guide.html#record-expenses
# Bill attachments: Zoho Books Help, "a maximum of 5 files, each of 5 MB"
#   https://www.zoho.com/us/books/help/bills/other-actions.html#attach-files-to-bill
# CRM record attachments have no documented size on the record-attachment
# endpoint. A limit is only applied when configured.
DEFAULT_MAX_UPLOAD_BYTES: Dict[str, int] = {
    "expense-receipt": 7 * 1024 * 1024,
    "bill-attachment": 5 * 1024 * 1024,
    "file-upload": WORKDRIVE_MAX_UPLOAD_BYTES,
    "new-version": WORKDRIVE_MAX_UPLOAD_BYTES,
}

_TARGET_LIMIT_ENV: Dict[str, str] = {
    "expense-receipt": "ZOHO_BRIDGE_MAX_BYTES_EXPENSE_RECEIPT",
    "bill-attachment": "ZOHO_BRIDGE_MAX_BYTES_BILL_ATTACHMENT",
    "record-attachment": "ZOHO_BRIDGE_MAX_BYTES_RECORD_ATTACHMENT",
    "file-upload": "ZOHO_BRIDGE_MAX_BYTES_FILE_UPLOAD",
    "new-version": "ZOHO_BRIDGE_MAX_BYTES_NEW_VERSION",
}

_LIMIT_LABELS: Dict[str, str] = {
    "expense-receipt": "7 MB",
    "bill-attachment": "5 MB",
    "file-upload": "250 MB",
    "new-version": "250 MB",
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
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".rtf": "application/rtf",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".zip": "application/zip",
    ".tar": "application/x-tar",
    ".gz": "application/gzip",
    ".7z": "application/x-7z-compressed",
    ".json": "application/json",
    ".xml": "application/xml",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".wav": "audio/wav",
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


def crm_base_url(dc: str) -> str:
    """Return the Zoho CRM API v8 base URL (e.g. https://www.zohoapis.eu/crm/v8)."""
    resolve_dc(dc)  # validate
    return f"https://www.{API_DC_MAP[dc.lower().strip()]}/crm/v8"


def workdrive_base_url(dc: str) -> str:
    """Return the WorkDrive API base URL (e.g. https://www.zohoapis.eu/workdrive)."""
    resolve_dc(dc)  # validate
    return f"https://www.{API_DC_MAP[dc.lower().strip()]}/workdrive"


def projects_base_url(dc: str) -> str:
    """Return the Zoho Projects API base URL (e.g. https://projectsapi.zoho.eu)."""
    resolve_dc(dc)  # validate
    return f"https://projectsapi.{resolve_dc(dc)}"


def workdrive_download_base_url(dc: str) -> str:
    """Return the WorkDrive download host base URL (e.g. https://download.zoho.eu)."""
    resolve_dc(dc)  # validate
    return f"https://{WORKDRIVE_DOWNLOAD_DC_MAP[dc.lower().strip()]}"


def validate_workdrive_resource_id(value: str, label: str) -> str:
    """
    Validate an opaque WorkDrive resource ID before URL construction.

    WorkDrive IDs are opaque alphanumeric strings, unlike the numeric IDs used
    by Books and CRM.
    """
    normalized = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", normalized):
        raise ValueError(
            f"{label} must be an opaque WorkDrive resource ID "
            "(letters, digits, underscore, hyphen). Resolve it with the WorkDrive "
            "MCP skill instead of deriving it from a path or file name."
        )
    return normalized


def validate_crm_module(module: str) -> str:
    """Validate a CRM module API name before interpolating it into a URL."""
    value = str(module or "").strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value):
        raise ValueError(
            "Invalid CRM module API name. Use the module's API name, for example "
            "Accounts, Contacts, Deals, Leads, or a custom module API name."
        )
    return value


def validate_zoho_id(value: str, label: str) -> str:
    """Validate a numeric Zoho record/attachment ID before URL construction."""
    normalized = str(value or "").strip()
    if not normalized.isdigit():
        raise ValueError(f"{label} must contain digits only")
    return normalized


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


def get_max_upload_bytes(
    target: str,
    override_bytes: Optional[int] = None,
    profile: Optional[str] = None,
) -> Optional[int]:
    """
    Return the maximum upload size in bytes for a target, or None if unconstrained.
    Precedence:
      1. Explicit override_bytes parameter (e.g. from CLI flag)
      2. Environment variable ZOHO_BRIDGE_[<PROFILE>_]MAX_BYTES_<TARGET>
      3. Global environment variable ZOHO_BRIDGE_MAX_UPLOAD_BYTES
      4. Documented default per target (DEFAULT_MAX_UPLOAD_BYTES)
    """
    if override_bytes is not None:
        if override_bytes <= 0:
            raise ValueError(f"Upload size limit must be positive, got {override_bytes}")
        return override_bytes

    if target not in _TARGET_LIMIT_ENV:
        valid = sorted(set(list(DEFAULT_MAX_UPLOAD_BYTES.keys()) + list(_TARGET_LIMIT_ENV.keys())))
        raise ValueError(
            f"Unknown target '{target}'. Valid targets: {', '.join(valid)}"
        )

    env_suffix = _TARGET_LIMIT_ENV[target].removeprefix("ZOHO_BRIDGE_")
    candidates = []
    if profile:
        candidates.append(f"ZOHO_BRIDGE_{profile.upper()}_{env_suffix}")
    candidates.append(f"ZOHO_BRIDGE_{env_suffix}")
    if profile:
        candidates.append(f"ZOHO_BRIDGE_{profile.upper()}_MAX_UPLOAD_BYTES")
    candidates.append("ZOHO_BRIDGE_MAX_UPLOAD_BYTES")

    for var in candidates:
        val = os.environ.get(var)
        if val:
            try:
                parsed = int(val.strip())
                if parsed <= 0:
                    raise ValueError(f"Limit in {var} must be positive, got {parsed}")
                return parsed
            except ValueError as exc:
                raise ValueError(f"Invalid integer in environment variable {var}: '{val}'") from exc

    return DEFAULT_MAX_UPLOAD_BYTES.get(target)


def validate_file_size(
    file_path: str,
    target: str,
    override_bytes: Optional[int] = None,
    profile: Optional[str] = None,
) -> int:
    """
    Validate that the local file size does not exceed the limit for target.
    Returns the file size in bytes on success, raises ValueError on violation.
    """
    size = os.path.getsize(file_path)
    limit = get_max_upload_bytes(target, override_bytes=override_bytes, profile=profile)
    if limit is not None and size > limit:
        label = _LIMIT_LABELS.get(target, f"{limit} bytes")
        if limit != DEFAULT_MAX_UPLOAD_BYTES.get(target):
            limit_desc = f"{limit} bytes (configured limit)"
        else:
            limit_desc = f"{limit} bytes ({label} limit)"
        raise ValueError(
            f"File '{Path(file_path).name}' is {size} bytes, which exceeds the "
            f"maximum upload size for '{target}' of {limit_desc}."
        )
    return size


def validate_file_extension(file_path: str, target: str) -> str:
    """
    Validate that the file's extension is in the target allowlist.
    Returns normalized lowercase extension (e.g. '.pdf').
    """
    ext = Path(file_path).suffix.lower()
    # Zoho's CRM v8 record-attachment documentation does not publish a file
    # extension allowlist. Do not invent one: send the file as
    # application/octet-stream when its MIME type is unknown and let the API
    # apply the account's real policy. An extension is still required so the
    # uploaded file has a meaningful name.
    if target == "record-attachment":
        if not ext:
            raise ValueError("CRM record attachments require a filename extension")
        return ext
    # WorkDrive publishes no fixed extension allowlist either. Blocked types are
    # an organization setting, reported by the API as D9236 (blocked list) or
    # D9237 (not in the org-allowed list). Require an extension and let the
    # account policy decide.
    if target in ("file-upload", "new-version"):
        if not ext:
            raise ValueError("WorkDrive uploads require a filename extension")
        return ext
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
    if status < 400 and (status == 204 or not body.strip()):
        return {}

    text = body.decode("utf-8", errors="replace")

    if status >= 400:
        if not body.strip():
            raise RuntimeError(f"{action_context} failed: HTTP {status} — empty response")
        # Check if error message is formatted as JSON
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                msg = data.get("message")
                code = data.get("code")
                if not msg and "data" in data and isinstance(data["data"], list) and data["data"]:
                    first = data["data"][0]
                    if isinstance(first, dict):
                        msg = first.get("message")
                        code = first.get("code")
                # WorkDrive JSON:API errors: {"errors": [{"id": "F6003", "title": "..."}]}
                if not msg and isinstance(data.get("errors"), list) and data["errors"]:
                    first_err = data["errors"][0]
                    if isinstance(first_err, dict):
                        msg = first_err.get("title") or first_err.get("detail") or first_err.get("message")
                        code = first_err.get("id") or first_err.get("code")
                if msg:
                    raise RuntimeError(
                        f"{action_context} failed (HTTP {status}): {msg} (code: {code})"
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
        # WorkDrive JSON:API errors inside HTTP 200 (if any)
        if isinstance(data.get("errors"), list) and data["errors"]:
            first_err = data["errors"][0]
            if isinstance(first_err, dict):
                err_msg = first_err.get("title") or first_err.get("detail") or "unknown WorkDrive error"
                err_code = first_err.get("id") or first_err.get("code")
                raise RuntimeError(f"{action_context} error (WorkDrive code {err_code}): {err_msg}")

        code = data.get("code")
        if code is not None and code not in (0, "SUCCESS", "success"):
            msg = data.get("message", "unknown error")
            raise RuntimeError(f"{action_context} error (Zoho code {code}): {msg}")
        if "data" in data and isinstance(data["data"], list) and data["data"]:
            first = data["data"][0]
            if isinstance(first, dict):
                status_str = first.get("status")
                item_code = first.get("code")
                if status_str and str(status_str).lower() in ("error", "failure"):
                    msg = first.get("message", "unknown error")
                    raise RuntimeError(f"{action_context} error (Zoho code {item_code}): {msg}")
                if item_code is not None and item_code not in ("SUCCESS", "success", 0):
                    msg = first.get("message", "unknown error")
                    raise RuntimeError(f"{action_context} error (Zoho code {item_code}): {msg}")

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
    max_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Upload expense receipt using multipart/form-data.
    POST /api/v3/expenses/{expense_id}/receipt?organization_id={org_id}
    """
    validate_file_extension(file_path, "expense-receipt")
    validate_file_size(file_path, "expense-receipt", override_bytes=max_bytes)
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
    max_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Upload bill attachment using multipart/form-data.
    POST /api/v3/bills/{bill_id}/attachment?organization_id={org_id}
    """
    validate_file_extension(file_path, "bill-attachment")
    validate_file_size(file_path, "bill-attachment", override_bytes=max_bytes)
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


# ---------------------------------------------------------------------------
# CRM API v8 Upload & Read-Back
# ---------------------------------------------------------------------------

def upload_crm_record_attachment(
    dc: str,
    access_token: str,
    module: str,
    record_id: str,
    file_path: str,
    max_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Upload attachment to a CRM record using multipart/form-data.
    POST /crm/v8/{module}/{record_id}/Attachments
    Multipart field name: 'file'
    """
    validate_file_extension(file_path, "record-attachment")
    validate_file_size(file_path, "record-attachment", override_bytes=max_bytes)
    module = validate_crm_module(module)
    record_id = validate_zoho_id(record_id, "CRM record ID")
    body, content_type = build_multipart_body(file_path, field_name="file")
    url = f"{crm_base_url(dc)}/{module}/{record_id}/Attachments"
    status, resp_bytes = api_request(
        url, access_token, data=body, content_type=content_type, method="POST"
    )
    return parse_zoho_response(resp_bytes, status, f"CRM {module} attachment upload")


def list_crm_record_attachments(
    dc: str,
    access_token: str,
    module: str,
    record_id: str,
    fields: str = "id,File_Name",
) -> List[Dict[str, Any]]:
    """
    List attachments for a CRM record.
    GET /crm/v8/{module}/{record_id}/Attachments?fields={fields}
    """
    module = validate_crm_module(module)
    record_id = validate_zoho_id(record_id, "CRM record ID")
    url = f"{crm_base_url(dc)}/{module}/{record_id}/Attachments?{urllib.parse.urlencode({'fields': fields})}"
    status, body = api_request(url, access_token, method="GET")
    if status == 204 or not body.strip():
        return []
    data = parse_zoho_response(body, status, f"List CRM {module} attachments")
    attachments = data.get("data", [])
    if not isinstance(attachments, list):
        return []
    return attachments


def download_crm_record_attachment(
    dc: str,
    access_token: str,
    module: str,
    record_id: str,
    attachment_id: str,
) -> bytes:
    """
    Download an attachment of a CRM record.
    GET /crm/v8/{module}/{record_id}/Attachments/{attachment_id}
    """
    module = validate_crm_module(module)
    record_id = validate_zoho_id(record_id, "CRM record ID")
    attachment_id = validate_zoho_id(attachment_id, "CRM attachment ID")
    url = f"{crm_base_url(dc)}/{module}/{record_id}/Attachments/{attachment_id}"
    status, body = api_request(url, access_token, method="GET")
    if status >= 400:
        err_text = body.decode("utf-8", errors="replace")
        raise RuntimeError(f"Failed to download CRM attachment: HTTP {status} — {err_text}")
    return body


def verify_crm_record_attachment(
    dc: str,
    access_token: str,
    module: str,
    record_id: str,
    file_name: str,
    expected_sha256: str,
    attachment_id: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Read back and verify the uploaded CRM attachment.
    1. Fetches attachment list via GET .../Attachments?fields=id,File_Name
    2. Identifies the attachment ID (matching attachment_id or file_name)
    3. Downloads the attachment via GET .../Attachments/{attachment_id}
    4. Compares SHA-256 hash.
    Returns (success_bool, message).
    """
    try:
        attachments = list_crm_record_attachments(dc, access_token, module, record_id)
    except Exception as exc:
        return False, f"Verification failed: unable to list CRM attachments ({exc})"

    target_id: Optional[str] = None
    if attachment_id:
        for att in attachments:
            if str(att.get("id")) == str(attachment_id):
                target_id = str(attachment_id)
                break
        if not target_id:
            return False, (
                f"Verification failed: newly uploaded attachment '{attachment_id}' "
                f"was not found on CRM {module} record {record_id}"
            )
    else:
        filename_matches = [
            att for att in attachments
            if att.get("File_Name") == file_name or att.get("file_name") == file_name
        ]
        if len(filename_matches) == 1 and filename_matches[0].get("id"):
            target_id = str(filename_matches[0]["id"])
        elif len(filename_matches) > 1:
            return False, (
                f"Verification failed: multiple CRM attachments named '{file_name}' "
                "exist; upload response did not identify the newly uploaded attachment"
            )

    if not target_id:
        return False, (
            f"Verification failed: Attachment '{file_name}' not found "
            f"on CRM {module} record {record_id}"
        )

    try:
        downloaded = download_crm_record_attachment(
            dc, access_token, module, record_id, target_id
        )
    except Exception as exc:
        return False, f"Verification failed: unable to read back CRM attachment ({exc})"

    downloaded_sha256 = sha256_bytes(downloaded)
    if downloaded_sha256 == expected_sha256:
        return True, f"Verified: SHA-256 match ({downloaded_sha256})"
    return False, (
        f"Verification failed: SHA-256 mismatch. "
        f"Expected {expected_sha256}, got {downloaded_sha256}"
    )


# ---------------------------------------------------------------------------
# WorkDrive API Upload & Read-Back
# ---------------------------------------------------------------------------

def upload_workdrive_file(
    dc: str,
    access_token: str,
    parent_id: str,
    file_path: str,
    filename: Optional[str] = None,
    override_name_exist: bool = False,
    max_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Upload a binary file into a WorkDrive folder using multipart/form-data.

    POST /workdrive/api/v1/upload
    Multipart fields:
      - 'content': binary file bytes (required, documented maximum 250 MB)
      - 'parent_id': destination folder ID (required)
      - 'filename': file name including its extension (optional)
      - 'override-name-exist': 'true' stores the upload as a new top version of
        an existing file with the same name. 'false' appends a timestamp.

    The same endpoint serves both a new file and a new version; the difference
    is override-name-exist.
    """
    target = "new-version" if override_name_exist else "file-upload"
    validate_file_extension(file_path, target)
    validate_file_size(file_path, target, override_bytes=max_bytes)
    parent_id = validate_workdrive_resource_id(parent_id, "WorkDrive parent folder ID")

    extra_fields: Dict[str, str] = {
        "parent_id": parent_id,
        "filename": filename or Path(file_path).name,
        "override-name-exist": "true" if override_name_exist else "false",
    }

    body, content_type = build_multipart_body(
        file_path, field_name="content", extra_fields=extra_fields
    )
    url = f"{workdrive_base_url(dc)}/api/v1/upload"
    status, resp_bytes = api_request(
        url, access_token, data=body, content_type=content_type, method="POST"
    )
    context = "WorkDrive new version upload" if override_name_exist else "WorkDrive file upload"
    return parse_zoho_response(resp_bytes, status, context)


def extract_workdrive_resource_id(upload_response: Dict[str, Any]) -> Optional[str]:
    """
    Extract the uploaded file's resource ID from a WorkDrive upload response.

    WorkDrive answers in JSON:API shape, with the resource ID inside
    data[0].attributes. Both 'resource_id' and the uppercase 'RESOURCE_ID'
    spelling appear in Zoho's own documentation.
    """
    if not isinstance(upload_response, dict):
        return None

    entries = upload_response.get("data")
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return None

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        attributes = entry.get("attributes")
        if isinstance(attributes, dict):
            for key in ("resource_id", "RESOURCE_ID"):
                value = attributes.get(key)
                if value:
                    return str(value)
        value = entry.get("id")
        if value:
            return str(value)
    return None


def download_workdrive_file(
    dc: str,
    access_token: str,
    resource_id: str,
    version: Optional[str] = None,
) -> bytes:
    """
    Download a WorkDrive file from the dedicated download host.

    GET https://download.zoho.<dc>/v1/workdrive/download/{resource_id}
    """
    resource_id = validate_workdrive_resource_id(resource_id, "WorkDrive resource ID")
    url = f"{workdrive_download_base_url(dc)}/v1/workdrive/download/{resource_id}"
    if version:
        url = f"{url}?{urllib.parse.urlencode({'version': version})}"

    status, body = api_request(url, access_token, method="GET")
    if status >= 400:
        err_text = body.decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Failed to download WorkDrive file: HTTP {status} \u2014 {err_text}"
        )
    return body


def verify_workdrive_file(
    dc: str,
    access_token: str,
    resource_id: str,
    expected_sha256: str,
    version: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Read back the uploaded WorkDrive file and compare its SHA-256 digest.
    Returns (success_bool, message).
    """
    try:
        downloaded = download_workdrive_file(
            dc, access_token, resource_id, version=version
        )
    except Exception as exc:
        return False, f"Verification failed: unable to read back WorkDrive file ({exc})"

    downloaded_sha256 = sha256_bytes(downloaded)
    if downloaded_sha256 == expected_sha256:
        return True, f"Verified: SHA-256 match ({downloaded_sha256})"
    return False, (
        f"Verification failed: SHA-256 mismatch. "
        f"Expected {expected_sha256}, got {downloaded_sha256}"
    )


# ---------------------------------------------------------------------------
# Organization & Portal discovery helpers (Issue #10)
# ---------------------------------------------------------------------------

def parse_books_6024_organizations(body: bytes) -> List[Dict[str, Any]]:
    """
    Extract candidate organizations from a Zoho Books 6024 error response.
    Returns a list of dicts with keys 'organization_id', 'name', and 'is_default_org'.
    """
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        return []

    orgs_raw = data.get("organizations")
    if not isinstance(orgs_raw, list):
        error_info = data.get("error_info")
        if isinstance(error_info, dict):
            for key in ("organizations", "organization_details", "orgs"):
                candidate = error_info.get(key)
                if isinstance(candidate, list):
                    orgs_raw = candidate
                    break
    if not isinstance(orgs_raw, list):
        orgs_raw = []

    results: List[Dict[str, Any]] = []
    for item in orgs_raw:
        if isinstance(item, dict):
            org_id = str(item.get("organization_id") or item.get("id") or "").strip()
            name = str(item.get("name") or item.get("organization_name") or "").strip()
            is_default = bool(item.get("is_default_org", False))
            if org_id:
                results.append({
                    "organization_id": org_id,
                    "name": name,
                    "is_default_org": is_default,
                })
    return results


def list_books_organizations(
    dc: str,
    access_token: str,
) -> List[Dict[str, Any]]:
    """
    List Zoho Books organizations accessible to the current access token.
    Requires optional OAuth scope: ZohoBooks.settings.READ or ZohoBooks.fullaccess.ALL.
    GET /books/v3/organizations
    """
    url = f"{books_base_url(dc)}/organizations"
    status, body = api_request(url, access_token, method="GET")
    data = parse_zoho_response(body, status, "list Books organizations")
    raw_orgs = data.get("organizations", [])
    results: List[Dict[str, Any]] = []
    for item in raw_orgs:
        if isinstance(item, dict):
            org_id = str(item.get("organization_id") or "").strip()
            name = str(item.get("name") or "").strip()
            if org_id:
                results.append({
                    "organization_id": org_id,
                    "name": name,
                    "is_default_org": bool(item.get("is_default_org", False)),
                    "currency_code": str(item.get("currency_code") or "").strip(),
                    "time_zone": str(item.get("time_zone") or "").strip(),
                })
    return results


def list_projects_portals(
    dc: str,
    access_token: str,
) -> List[Dict[str, Any]]:
    """
    List Zoho Projects portals accessible to the current access token.
    Requires OAuth scope: ZohoProjects.portals.READ.
    GET /api/v3/portals
    """
    url = f"{projects_base_url(dc)}/api/v3/portals"
    status, body = api_request(url, access_token, method="GET")
    data = parse_zoho_response(body, status, "list Projects portals")

    # Projects V3 returns a list of portal objects directly or under 'portals'
    raw_portals: List[Any] = []
    if isinstance(data, list):
        raw_portals = data
    elif isinstance(data, dict):
        if "portals" in data and isinstance(data["portals"], list):
            raw_portals = data["portals"]
        else:
            raw_portals = [data]

    results: List[Dict[str, Any]] = []
    for item in raw_portals:
        if isinstance(item, dict):
            portal_id = str(item.get("id") or item.get("id_string") or "").strip()
            name = str(item.get("portal_name") or item.get("name") or "").strip()
            if portal_id:
                results.append({
                    "portal_id": portal_id,
                    "name": name,
                    "is_default_portal": bool(item.get("is_default_portal", False) or item.get("default", False)),
                    "project_plan": str(item.get("project_plan") or item.get("plan") or "").strip(),
                })
    return results
